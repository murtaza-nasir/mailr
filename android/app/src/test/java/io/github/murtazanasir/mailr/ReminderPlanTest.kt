package io.github.murtazanasir.mailr

import android.app.Application
import org.junit.runner.RunWith
import org.robolectric.RobolectricTestRunner
import org.robolectric.annotation.Config
import io.github.murtazanasir.mailr.data.api.CalendarEvent
import io.github.murtazanasir.mailr.data.api.Occurrence
import io.github.murtazanasir.mailr.data.api.TaskItem
import io.github.murtazanasir.mailr.data.db.ReminderEntity
import io.github.murtazanasir.mailr.data.reminders.Kind
import io.github.murtazanasir.mailr.data.reminders.RState
import io.github.murtazanasir.mailr.data.reminders.ReminderConfig
import io.github.murtazanasir.mailr.data.reminders.ReminderGrouping
import io.github.murtazanasir.mailr.data.reminders.ReminderPlan
import io.github.murtazanasir.mailr.data.reminders.SnoozeTimes
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test
import java.time.ZoneId
import java.time.ZonedDateTime

/** Pure planning, diffing, grouping and snooze-time rules (no Android). */
@RunWith(RobolectricTestRunner::class)
@Config(application = Application::class, sdk = [34])
class ReminderPlanTest {
    private val zone = ZoneId.of("America/Chicago")
    private val now = ZonedDateTime.of(2026, 9, 29, 12, 0, 0, 0, zone)
    private val nowMs = now.toInstant().toEpochMilli()
    private val cfg = ReminderConfig(defaultTimedEvent = 10, defaultAllDayEvent = null, defaultTask = true, morningHour = 9)

    private fun iso(z: ZonedDateTime) = z.toOffsetDateTime().toString()
    private fun utc(z: ZonedDateTime) = Occurrence.utc(z.toInstant())
    private fun ev(uid: String, start: ZonedDateTime, mins: Long = 60, reminders: List<Int> = emptyList(), status: String? = null,
                   my: String? = null) =
        CalendarEvent(uid, "Event $uid", iso(start), iso(start.plusMinutes(mins)), reminders = reminders, status = status, myStatus = my)

    // ---------------- plan ----------------

    @Test fun eventsUseTheirOwnRemindersElseTheDefault() {
        val p = ReminderPlan.plan(listOf(ev("a", now.plusHours(2), reminders = listOf(30, 5)), ev("b", now.plusHours(3))),
            emptyList(), cfg, nowMs, zone)
        // Keys carry the canonical occurrence (UTC), whatever offset the start came with.
        assertEquals(setOf("e|a|${utc(now.plusHours(2))}|30", "e|a|${utc(now.plusHours(2))}|5", "e|b|${utc(now.plusHours(3))}|10"),
            p.map { it.key }.toSet())
        val a30 = p.first { it.key.endsWith("|30") }
        assertEquals(now.plusHours(2).minusMinutes(30).toInstant().toEpochMilli(), a30.fireAt)
    }

    @Test fun skipsCancelledDeclinedEndedAndFarEvents() {
        val p = ReminderPlan.plan(listOf(
            ev("x", now.plusHours(1), status = "CANCELLED"),
            ev("d", now.plusHours(1), my = "DECLINED"),
            ev("old", now.minusHours(3)),
            ev("far", now.plusDays(20)),
        ), emptyList(), cfg, nowMs, zone)
        assertTrue(p.isEmpty())
    }

    @Test fun allDayEventsAnchorAtLocalMidnight() {
        val e = CalendarEvent("h", "Holiday", "2026-09-30", "2026-10-01", allDay = true, reminders = listOf(-540, 900))
        val p = ReminderPlan.plan(listOf(e), emptyList(), cfg, nowMs, zone).associateBy { it.key.substringAfterLast('|') }
        assertEquals(ZonedDateTime.of(2026, 9, 30, 9, 0, 0, 0, zone).toInstant().toEpochMilli(), p["-540"]!!.fireAt)
        assertEquals(ZonedDateTime.of(2026, 9, 29, 9, 0, 0, 0, zone).toInstant().toEpochMilli(), p["900"]!!.fireAt)
        // No default for all-day events without their own reminder.
        assertTrue(ReminderPlan.plan(listOf(e.copy(reminders = emptyList())), emptyList(), cfg, nowMs, zone).isEmpty())
    }

    @Test fun tasksRemindAtDueTimeOrMorningForDateOnly() {
        val timed = TaskItem("t1", "Call", iso(now.plusHours(5)))
        val dated = TaskItem("t2", "Pay", "2026-10-02")
        val done = TaskItem("t3", "Done", iso(now.plusHours(1)), status = "completed")
        val none = TaskItem("t4", "Someday")
        val p = ReminderPlan.plan(emptyList(), listOf(timed, dated, done, none), cfg, nowMs, zone).associateBy { it.itemUid }
        assertEquals(setOf("t1", "t2"), p.keys)
        assertEquals(now.plusHours(5).toInstant().toEpochMilli(), p["t1"]!!.fireAt)
        assertEquals(ZonedDateTime.of(2026, 10, 2, 9, 0, 0, 0, zone).toInstant().toEpochMilli(), p["t2"]!!.fireAt)
        assertTrue(ReminderPlan.plan(emptyList(), listOf(timed), cfg.copy(tasks = false), nowMs, zone).isEmpty())
    }

    @Test fun planningIsDeterministic() {
        val evs = listOf(ev("a", now.plusHours(2)), ev("b", now.plusDays(1)))
        assertEquals(ReminderPlan.plan(evs, emptyList(), cfg, nowMs, zone), ReminderPlan.plan(evs.reversed().reversed(), emptyList(), cfg, nowMs, zone))
    }

    // ---------------- reconcile ----------------

    private fun plan(vararg e: CalendarEvent) = ReminderPlan.plan(e.toList(), emptyList(), cfg, nowMs, zone)
    private fun live(vararg e: CalendarEvent) = ReminderPlan.liveKeys(e.toList(), emptyList())
    private fun rows(r: List<ReminderEntity>, vararg changes: Pair<String, (ReminderEntity) -> ReminderEntity>): List<ReminderEntity> =
        r.map { row -> changes.firstOrNull { it.first == row.itemUid }?.second?.invoke(row) ?: row }

    @Test fun newRemindersAreInsertedOnce() {
        val a = ev("a", now.plusHours(2))
        val first = ReminderPlan.reconcile(emptyList(), plan(a), live(a), nowMs)
        assertEquals(1, first.upsert.size)
        assertEquals(RState.PENDING, first.upsert[0].state)
        // Same plan again: nothing to do.
        val second = ReminderPlan.reconcile(first.upsert, plan(a), live(a), nowMs)
        assertTrue(second.upsert.isEmpty() && second.delete.isEmpty() && second.cancel.isEmpty())
    }

    @Test fun movedEventMovesItsPendingReminder() {
        val a = ev("a", now.plusHours(2))
        val stored = ReminderPlan.reconcile(emptyList(), plan(a), live(a), nowMs).upsert
        val moved = ev("a", now.plusHours(4))
        val r = ReminderPlan.reconcile(stored, plan(moved), live(moved), nowMs)
        // The occurrence is part of the key, so the old reminder goes and a new one comes.
        assertEquals(listOf(stored[0].key), r.delete)
        assertEquals(now.plusHours(4).minusMinutes(10).toInstant().toEpochMilli(), r.upsert.single().fireAt)
    }

    @Test fun shownAndDismissedRowsKeepTheirStateAcrossSyncs() {
        val a = ev("a", now.plusMinutes(5))
        val stored = rows(ReminderPlan.reconcile(emptyList(), plan(a), live(a), nowMs).upsert, "a" to { it.copy(state = RState.SHOWN) })
        val r = ReminderPlan.reconcile(stored, plan(a), live(a), nowMs)
        assertTrue(r.upsert.isEmpty() && r.delete.isEmpty())
        // Renamed while showing: updated quietly, still SHOWN.
        val renamed = a.copy(summary = "Renamed")
        val r2 = ReminderPlan.reconcile(stored, plan(renamed), live(renamed), nowMs)
        assertEquals(RState.SHOWN, r2.upsert.single().state)
        assertEquals("Renamed", r2.repost.single().title)
    }

    @Test fun snoozeSurvivesSyncAndKeepsItsTime() {
        val a = ev("a", now.plusMinutes(5))
        val snoozeAt = nowMs + 60 * 60_000L
        val stored = rows(ReminderPlan.reconcile(emptyList(), plan(a), live(a), nowMs).upsert,
            "a" to { it.copy(state = RState.PENDING, snoozed = true, fireAt = snoozeAt) })
        val r = ReminderPlan.reconcile(stored, plan(a), live(a), nowMs)
        assertTrue(r.upsert.isEmpty() && r.delete.isEmpty())
        // Even when the plan no longer contains it (reminder offset changed), the snooze stays while the event exists.
        val r2 = ReminderPlan.reconcile(stored, plan(a.copy(reminders = listOf(30))), live(a), nowMs)
        assertFalse(stored[0].key in r2.delete)
    }

    @Test fun deletedOrCompletedItemsTakeTheirNotificationsDown() {
        val a = ev("a", now.plusMinutes(5))
        val stored = rows(ReminderPlan.reconcile(emptyList(), plan(a), live(a), nowMs).upsert, "a" to { it.copy(state = RState.SHOWN) })
        val r = ReminderPlan.reconcile(stored, emptyList(), emptySet(), nowMs)
        assertEquals(listOf(stored[0].key), r.delete)
        assertEquals(stored[0].key, r.cancel.single().key)
    }

    @Test fun lateDiscoveredReminderStillFiresIfTheEventIsAhead() {
        // Synced 5 minutes before the start; the 10-minute reminder is 5 minutes late but useful.
        val a = ev("a", now.plusMinutes(5))
        val r = ReminderPlan.reconcile(emptyList(), plan(a), live(a), nowMs).upsert.single()
        assertEquals(RState.PENDING, r.state)
        assertTrue(r.fireAt >= nowMs)
        // An event that already started: its reminder is not worth firing.
        val b = ev("b", now.minusMinutes(5), mins = 60)
        assertEquals(RState.DISMISSED, ReminderPlan.reconcile(emptyList(), plan(b), live(b), nowMs).upsert.single().state)
    }

    @Test fun relevanceAndMissed() {
        val row = ReminderEntity("k", Kind.EVENT, "a", null, "t", "d", fireAt = nowMs - 3 * 60_000, plannedAt = nowMs,
            startAt = nowMs + 10 * 60_000, endAt = nowMs + 70 * 60_000, state = RState.PENDING, updatedAt = nowMs)
        assertTrue(ReminderPlan.missed(row, nowMs))
        assertFalse(ReminderPlan.missed(row.copy(fireAt = nowMs - 30_000), nowMs))
        assertTrue(ReminderPlan.relevant(row, nowMs))
        assertFalse(ReminderPlan.relevant(row.copy(endAt = nowMs - 1), nowMs))
        assertTrue(ReminderPlan.relevant(row.copy(kind = Kind.TASK, endAt = null), nowMs))
    }

    // ---------------- grouping ----------------

    private fun shown(uid: String, start: Long, kind: String = Kind.EVENT, missed: Boolean = false) =
        ReminderEntity("k$uid", kind, uid, null, "Title $uid", "detail", start, start, start, null, RState.SHOWN, missed = missed, updatedAt = 0)

    @Test fun summaryListsEachReminderWithACount() {
        val spec = ReminderGrouping.summary(Kind.EVENT, listOf(shown("b", 20), shown("a", 10), shown("t", 5, Kind.TASK)))!!
        assertEquals(2, spec.count)
        assertEquals("2 event reminders", spec.title)
        assertEquals(listOf("Title a · detail", "Title b · detail"), spec.lines)
        assertEquals(0, spec.more)
    }

    @Test fun summaryForOneStaysAndForNoneIsGone() {
        assertEquals("1 task reminder", ReminderGrouping.summary(Kind.TASK, listOf(shown("t", 5, Kind.TASK)))!!.title)
        assertNull(ReminderGrouping.summary(Kind.TASK, emptyList()))
        assertNull(ReminderGrouping.summary(Kind.EVENT, listOf(shown("t", 5, Kind.TASK))))
    }

    @Test fun summaryCapsLinesAndMarksMissed() {
        val many = (1..9).map { shown("e$it", it.toLong(), missed = it == 1) }
        val spec = ReminderGrouping.summary(Kind.EVENT, many)!!
        assertEquals(ReminderGrouping.MAX_LINES, spec.lines.size)
        assertEquals(3, spec.more)
        assertTrue(spec.lines.first().startsWith("Missed: "))
    }

    // ---------------- snooze ----------------

    @Test fun snoozeChoicesAreSortedAndIncludeStartAndMorning() {
        val start = now.plusMinutes(30)
        val o = SnoozeTimes.options(now, Kind.EVENT, start, 9)
        assertEquals(listOf("5m", "15m", "start", "1h", "morning"), o.map { it.id })
        assertEquals(now.plusMinutes(5), o[0].at)
        assertEquals(ZonedDateTime.of(2026, 9, 30, 9, 0, 0, 0, zone), o.last().at)
        assertEquals("Tomorrow morning", o.last().label)
        // Tasks have no "when it starts".
        assertTrue(SnoozeTimes.options(now, Kind.TASK, null).none { it.id == "start" })
    }

    @Test fun startOptionDroppedWhenTooCloseOrDuplicate() {
        // Starts in 15 min: "At start time" would equal "15 minutes"; only one stays.
        val o = SnoozeTimes.options(now, Kind.EVENT, now.plusMinutes(15), 9)
        assertEquals(1, o.count { it.at == now.plusMinutes(15) })
        assertTrue(SnoozeTimes.options(now, Kind.EVENT, now.plusMinutes(1), 9).none { it.id == "start" })
    }

    @Test fun morningInTheSmallHoursIsThisMorning() {
        val night = ZonedDateTime.of(2026, 9, 29, 1, 30, 0, 0, zone)
        assertEquals(ZonedDateTime.of(2026, 9, 29, 8, 0, 0, 0, zone), SnoozeTimes.morning(night, 8))
        assertEquals("This morning", SnoozeTimes.options(night, Kind.TASK, null, 8).last().label)
    }

    @Test fun tomorrowMorningAcrossDstIsNineLocal() {
        // US DST ends on Nov 1, 2026: 9 am the next day is still 9 am local.
        val sat = ZonedDateTime.of(2026, 10, 31, 22, 0, 0, 0, zone)
        val m = SnoozeTimes.morning(sat, 9)
        assertEquals(9, m.hour)
        assertEquals(1, m.dayOfMonth)
        assertEquals(12, java.time.Duration.between(sat, m).toHours())
        // "1 hour" across the fall-back hour is one real hour.
        val late = ZonedDateTime.of(2026, 11, 1, 0, 30, 0, 0, zone)
        assertEquals(60, java.time.Duration.between(late, SnoozeTimes.resolve("1h", late, null)!!).toMinutes())
    }

    /** Server parity: an all-day "1 day before at 9:00" across the fall-back change is 9:00 on the wall clock. */
    @Test fun allDayRemindersKeepTheWallClockAcrossDst() {
        // US DST ends on Sun 1 Nov 2026; the event is on Mon 2 Nov, the reminder 1 day before at 9:00 (900 min before midnight).
        val e = CalendarEvent("d", "Election day", "2026-11-02", "2026-11-03", allDay = true, reminders = listOf(900))
        val t = io.github.murtazanasir.mailr.data.api.TaskItem("t", "File the form", "2026-11-02", reminders = listOf(1440 - 540))
        val at = ZonedDateTime.of(2026, 10, 30, 12, 0, 0, 0, zone).toInstant().toEpochMilli()
        val p = ReminderPlan.plan(listOf(e), listOf(t), cfg, at, zone)
        val want = ZonedDateTime.of(2026, 11, 1, 9, 0, 0, 0, zone).toInstant().toEpochMilli()
        assertEquals(want, p.single { it.kind == Kind.EVENT }.fireAt)
        assertEquals(want, p.single { it.kind == Kind.TASK }.fireAt)
    }

    /**
     * Server parity (planner.fire_time) on the fall-back day itself: an all-day event on Sun 1 Nov
     * reminds at 9:00 CDT the day before; a date-only task's morning default is 9:00 CST on the day;
     * a timed event an exact 24 hours before, which is 10:00 on the wall clock across the change.
     */
    @Test fun dstDayParityWithTheServerPlanner() {
        val at = ZonedDateTime.of(2026, 10, 30, 12, 0, 0, 0, zone).toInstant().toEpochMilli()
        val allDay = CalendarEvent("d1", "Fair", "2026-11-01", "2026-11-02", allDay = true, reminders = listOf(900))
        val timed = ev("t1", ZonedDateTime.of(2026, 11, 1, 9, 0, 0, 0, zone), reminders = listOf(1440))
        val task = io.github.murtazanasir.mailr.data.api.TaskItem("k1", "Clocks", "2026-11-01")
        val p = ReminderPlan.plan(listOf(allDay, timed), listOf(task), cfg, at, zone).associateBy { it.itemUid }
        assertEquals(ZonedDateTime.of(2026, 10, 31, 9, 0, 0, 0, zone).toInstant().toEpochMilli(), p.getValue("d1").fireAt)
        assertEquals(ZonedDateTime.of(2026, 11, 1, 9, 0, 0, 0, zone).toInstant().toEpochMilli(), p.getValue("k1").fireAt)
        val start = ZonedDateTime.of(2026, 11, 1, 9, 0, 0, 0, zone)
        assertEquals(start.toInstant().toEpochMilli() - 24 * 3_600_000L, p.getValue("t1").fireAt)
        assertEquals(10, java.time.Instant.ofEpochMilli(p.getValue("t1").fireAt).atZone(zone).hour)
        // The keys are the server's: e|uid|occurrence|minutes and t|uid|due|minutes.
        assertEquals("e|d1|2026-11-01|900", p.getValue("d1").key)
        assertEquals("t|k1|2026-11-01|-540", p.getValue("k1").key)
    }

    /** task.remind_at: an absolute reminder (reminders_at) plans at its instant, keyed by its minutes before the due. */
    @Test fun absoluteTaskRemindersPlanAtTheirInstant() {
        val due = now.plusDays(1).withHour(15)
        val at = due.minusMinutes(90)
        val t = io.github.murtazanasir.mailr.data.api.TaskItem("k2", "Report", iso(due), remindersAt = listOf(at.toInstant().toString()))
        val p = ReminderPlan.plan(emptyList(), listOf(t), cfg, nowMs, zone).single()
        assertEquals(at.toInstant().toEpochMilli(), p.fireAt)
        assertEquals("t|k2|${t.dueKey}|90", p.key)
    }
}
