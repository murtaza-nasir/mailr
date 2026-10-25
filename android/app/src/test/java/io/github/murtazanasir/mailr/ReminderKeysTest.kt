package io.github.murtazanasir.mailr

import android.app.Application
import androidx.test.core.app.ApplicationProvider
import io.github.murtazanasir.mailr.data.api.ApiClient
import io.github.murtazanasir.mailr.data.api.CalendarApi
import io.github.murtazanasir.mailr.data.api.CalendarEvent
import io.github.murtazanasir.mailr.data.api.MailrApi
import io.github.murtazanasir.mailr.data.api.MailrJson
import io.github.murtazanasir.mailr.data.api.Occurrence
import io.github.murtazanasir.mailr.data.api.ReminderKeys
import io.github.murtazanasir.mailr.data.api.RemoteReminder
import io.github.murtazanasir.mailr.data.api.TaskItem
import io.github.murtazanasir.mailr.data.auth.SessionStore
import io.github.murtazanasir.mailr.data.cal.CalendarRepository
import io.github.murtazanasir.mailr.data.db.CalEventEntity
import io.github.murtazanasir.mailr.data.db.CalTaskEntity
import io.github.murtazanasir.mailr.data.db.MailrDb
import io.github.murtazanasir.mailr.data.db.ReminderEntity
import io.github.murtazanasir.mailr.data.reminders.Kind
import io.github.murtazanasir.mailr.data.reminders.RState
import io.github.murtazanasir.mailr.data.reminders.ReminderConfig
import io.github.murtazanasir.mailr.data.reminders.ReminderEngine
import io.github.murtazanasir.mailr.data.reminders.ReminderPlan
import io.github.murtazanasir.mailr.data.reminders.ReminderSink
import io.github.murtazanasir.mailr.data.reminders.SummarySpec
import io.github.murtazanasir.mailr.data.repo.MailRepository
import kotlinx.coroutines.test.runTest
import org.junit.After
import org.junit.Assert.assertEquals
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
 * The canonical occurrence string and reminder keys: the same strings
 * the server builds (Python isoformat in UTC, or the date), and stored keys in older forms
 * normalised so a reminder handled before the upgrade never fires again.
 */
@RunWith(RobolectricTestRunner::class)
@Config(application = Application::class, sdk = [34])
class ReminderKeysTest {
    private val zone = ZoneId.of("America/New_York")

    // ---------------- canonical strings (parity with the server's `_occ`) ----------------

    @Test fun canonicalFormsMatchTheServer() {
        val cases = mapOf(
            "2026-10-02T15:00:00+00:00" to "2026-10-02T15:00:00+00:00",
            "2026-10-02T15:00:00Z" to "2026-10-02T15:00:00+00:00",
            "2026-10-02T11:00:00-04:00" to "2026-10-02T15:00:00+00:00",
            "2026-10-02T16:00+01:00" to "2026-10-02T15:00:00+00:00",
            "20261002T150000Z" to "2026-10-02T15:00:00+00:00",
            // Floating (no offset): read in the user's zone, like the server.
            "20261002T110000" to "2026-10-02T15:00:00+00:00",
            "2026-10-02T11:00:00" to "2026-10-02T15:00:00+00:00",
            "2026-10-02" to "2026-10-02",
            "20261002" to "2026-10-02",
            "2026-10-02T15:00:00.250000+00:00" to "2026-10-02T15:00:00.250000+00:00",
            "" to "",
        )
        cases.forEach { (input, want) -> assertEquals(input, want, Occurrence.canonical(input, zone)) }
        assertEquals("", Occurrence.canonical(null, zone))
    }

    @Test fun eventKeysUseTheServersOccurrenceField() {
        // The server's field wins over anything the phone could format itself.
        val e = CalendarEvent("abc@x", "Budget", "2026-10-05T09:00:00+00:00", recurrenceId = "2026-10-05T05:00:00-04:00",
            occurrenceId = "2026-10-05T09:00:00+00:00", recurring = true, reminders = listOf(10))
        val now = ZonedDateTime.of(2026, 10, 5, 1, 0, 0, 0, zone).toInstant().toEpochMilli()
        val p = ReminderPlan.plan(listOf(e), emptyList(), ReminderConfig(), now, zone).single()
        assertEquals("e|abc@x|2026-10-05T09:00:00+00:00|10", p.key)
        assertEquals("abc@x|2026-10-05T09:00:00+00:00", e.key)
        // A server before the build sends no `occurrence`: the phone uses the same canonical string.
        assertEquals("e|abc@x|2026-10-05T09:00:00+00:00|10",
            ReminderPlan.plan(listOf(e.copy(occurrenceId = null)), emptyList(), ReminderConfig(), now, zone).single().key)
    }

    @Test fun taskKeysUseTheCanonicalDue() {
        val now = ZonedDateTime.of(2026, 10, 2, 8, 0, 0, 0, zone).toInstant().toEpochMilli()
        val timed = TaskItem("t@x", "Pay", "2026-10-02T11:00:00-04:00")
        val dated = TaskItem("d@x", "Plan", "2026-10-03")
        val keys = ReminderPlan.plan(emptyList(), listOf(timed, dated), ReminderConfig(), now, zone).map { it.key }.toSet()
        assertEquals(setOf("t|t@x|2026-10-02T15:00:00+00:00|0", "t|d@x|2026-10-03|-540"), keys)
    }

    @Test fun normalizeRewritesOnlyTheOccurrence() {
        assertEquals("e|a|b@x|2026-10-02T15:00:00+00:00|10", ReminderKeys.normalize("e|a|b@x|20261002T150000Z|10", zone))
        assertEquals("t|t@x|2026-10-02T15:00:00+00:00|0", ReminderKeys.normalize("t|t@x|2026-10-02T11:00:00-04:00|0", zone))
        assertEquals("e|u|2026-10-02|-540", ReminderKeys.normalize("e|u|2026-10-02|-540", zone))
        assertEquals("not-a-key", ReminderKeys.normalize("not-a-key", zone))
    }

    @Test fun normalizeKeepsTheFurthestState() {
        fun row(key: String, state: String, snoozed: Boolean = false) =
            ReminderEntity(key, Kind.EVENT, "u", key.split('|')[2], "T", "", 0, 0, null, null, state, snoozed = snoozed, updatedAt = 0)
        val legacy = row("e|u|2026-10-02T11:00:00-04:00|10", RState.DISMISSED)
        val fresh = row("e|u|2026-10-02T15:00:00+00:00|10", RState.PENDING)
        val n = ReminderPlan.normalize(listOf(legacy, fresh), zone)
        assertEquals(listOf(legacy.key), n.delete)
        assertEquals(RState.DISMISSED, n.upsert.single { it.key == fresh.key }.state)
        assertEquals("2026-10-02T15:00:00+00:00", n.upsert.single().occurrence)
        assertTrue(ReminderPlan.normalize(listOf(fresh), zone).upsert.isEmpty())
    }

    // ---------------- the engine: handled before the upgrade stays handled ----------------

    private lateinit var db: MailrDb
    private lateinit var engine: ReminderEngine
    private val t0 = ZonedDateTime.of(2026, 10, 2, 10, 30, 0, 0, zone)
    private var now = t0.toInstant().toEpochMilli()

    private class Shade : ReminderSink {
        val active = linkedMapOf<String, ReminderEntity>()
        val alerts = mutableListOf<String>()
        override fun post(r: ReminderEntity, alert: Boolean) { active[r.key] = r; if (alert) alerts += r.key }
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
        engine = ReminderEngine(db.reminders(), cal, { ReminderConfig(defaultTimedEvent = 10) }, shade, {},
            now = { now }, zone = { zone })
    }

    @After fun tearDown() = db.close()

    @Test fun legacyKeysHandledBeforeTheUpgradeDoNotFireAgain() = runTest {
        // An event at 11:00 New York (15:00 UTC), stored by the new server with its canonical occurrence.
        val start = t0.withHour(11).withMinute(0)
        val e = CalendarEvent("e1", "Budget review", "2026-10-02T15:00:00+00:00", "2026-10-02T16:00:00+00:00",
            occurrenceId = "2026-10-02T15:00:00+00:00")
        db.calendar().putEvents(listOf(CalEventEntity(e.key, "e1", start.toInstant().toEpochMilli(), start.plusHours(1).toInstant().toEpochMilli(),
            false, null, MailrJson.encodeToString(CalendarEvent.serializer(), e))))
        val t = TaskItem("t1", "Send the numbers", "2026-10-02T14:40:00+00:00")
        db.calendar().putTasks(listOf(CalTaskEntity("t1", null, null, false, t0.withMinute(40).toInstant().toEpochMilli(),
            MailrJson.encodeToString(TaskItem.serializer(), t))))
        // What an older build stored: the start with a local offset, and the compact form a web
        // client used. Both were dismissed.
        db.reminders().put(listOf(
            ReminderEntity("e|e1|2026-10-02T11:00:00-04:00|10", Kind.EVENT, "e1", "2026-10-02T11:00:00-04:00", "Budget review", "",
                start.minusMinutes(10).toInstant().toEpochMilli(), start.minusMinutes(10).toInstant().toEpochMilli(),
                null, null, RState.DISMISSED, updatedAt = 0),
            ReminderEntity("t|t1|20261002T144000Z|0", Kind.TASK, "t1", null, "Send the numbers", "",
                t0.withMinute(40).toInstant().toEpochMilli(), 0, null, null, RState.DISMISSED, updatedAt = 0),
        ))

        engine.resync()
        now = start.minusMinutes(5).toInstant().toEpochMilli()
        engine.fire()

        assertTrue("nothing fires again: ${shade.alerts}", shade.alerts.isEmpty())
        val rows = db.reminders().all().associateBy { it.key }
        assertEquals(setOf("e|e1|2026-10-02T15:00:00+00:00|10", "t|t1|2026-10-02T14:40:00+00:00|0"), rows.keys)
        assertTrue(rows.values.all { it.state == RState.DISMISSED })
        assertEquals("2026-10-02T15:00:00+00:00", rows.getValue("e|e1|2026-10-02T15:00:00+00:00|10").occurrence)
    }

    @Test fun aLegacyKeyFromTheServerLandsOnTheCanonicalRow() = runTest {
        val start = t0.withHour(11).withMinute(0)
        val e = CalendarEvent("e1", "Budget review", "2026-10-02T15:00:00+00:00", "2026-10-02T16:00:00+00:00",
            occurrenceId = "2026-10-02T15:00:00+00:00")
        db.calendar().putEvents(listOf(CalEventEntity(e.key, "e1", start.toInstant().toEpochMilli(), start.plusHours(1).toInstant().toEpochMilli(),
            false, null, MailrJson.encodeToString(CalendarEvent.serializer(), e))))
        engine.resync()
        val key = "e|e1|2026-10-02T15:00:00+00:00|10"
        assertEquals(RState.PENDING, db.reminders().get(key)!!.state)
        // Another device handled it, and an older server reports the key in its own form.
        engine.applyRemote(listOf(RemoteReminder("e|e1|2026-10-02T11:00:00-04:00|10", "e", "e1", "2026-10-02T11:00:00-04:00", 10, "handled")))
        assertEquals(RState.DISMISSED, db.reminders().get(key)!!.state)
        assertNull(db.reminders().get("e|e1|2026-10-02T11:00:00-04:00|10"))
    }
}
