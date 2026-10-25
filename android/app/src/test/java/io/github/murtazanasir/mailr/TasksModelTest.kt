// ACT-SURFACES: android.editor android.tasks android.share
package io.github.murtazanasir.mailr

import android.app.Application
import org.junit.runner.RunWith
import org.robolectric.RobolectricTestRunner
import org.robolectric.annotation.Config
import io.github.murtazanasir.mailr.data.api.CalendarCollection
import io.github.murtazanasir.mailr.data.api.TaskItem
import io.github.murtazanasir.mailr.data.share.SharedContent
import io.github.murtazanasir.mailr.ui.calendar.TokenKind
import io.github.murtazanasir.mailr.ui.tasks.DueChip
import io.github.murtazanasir.mailr.ui.tasks.GroupOptions
import io.github.murtazanasir.mailr.ui.tasks.QuickPrefill
import io.github.murtazanasir.mailr.ui.tasks.QuickTask
import io.github.murtazanasir.mailr.ui.tasks.ShareTask
import io.github.murtazanasir.mailr.ui.tasks.Smart
import io.github.murtazanasir.mailr.ui.tasks.TaskGroups
import io.github.murtazanasir.mailr.ui.tasks.TaskSearch
import io.github.murtazanasir.mailr.ui.tasks.TaskView
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test
import java.time.LocalDate
import java.time.ZoneId
import java.time.ZonedDateTime

/**
 * The Tasks screen's pure parts: the smart lists and their sections,
 * the counts, the due chip, search operators, quick add → TaskDraft (task.create) and a share
 * → task (android.share).
 */
@RunWith(RobolectricTestRunner::class)
@Config(application = Application::class, sdk = [34])
class TasksModelTest {
    /**
     * editorDraft writes Compose state (TaskEditorState) outside a composition. Under Robolectric that
     * posts the snapshot apply to the main looper, which Robolectric clears after the test; a Compose
     * test earlier in the JVM has started the apply loop, so the lost message wedges Compose for every
     * later Compose test. Run the apply before the teardown.
     */
    @org.junit.After fun applyStateWrites() {
        androidx.compose.runtime.snapshots.Snapshot.sendApplyNotifications()
        org.robolectric.Shadows.shadowOf(android.os.Looper.getMainLooper()).idle()
    }

    private val zone = ZoneId.of("America/Chicago")
    // Thursday 1 Oct 2026, noon.
    private val now = ZonedDateTime.of(2026, 10, 1, 12, 0, 0, 0, zone)
    private val nowMs = now.toInstant().toEpochMilli()
    private val today = now.toLocalDate()
    private fun iso(t: ZonedDateTime) = t.toOffsetDateTime().toString()
    private val lists = listOf(CalendarCollection("L1", "Work", tasks = true), CalendarCollection("L2", "Home", tasks = true))

    private val tasks = listOf(
        TaskItem("late", "Late report", iso(now.minusHours(2)), list = "L1"),
        TaskItem("old", "Old one", today.minusDays(3).toString(), list = "L1"),
        TaskItem("today", "Due tonight", iso(now.plusHours(6)), list = "L2", priority = 1),
        TaskItem("todayDate", "Some time today", today.toString(), list = "L2"),
        TaskItem("started", "Started already", null, start = iso(now.minusDays(1)), list = "L1"),
        TaskItem("deferred", "Not yet", today.toString(), start = today.plusDays(2).toString(), list = "L1"),
        TaskItem("tmrw", "Tomorrow", today.plusDays(1).toString(), list = "L2", conversationId = 7),
        TaskItem("sat", "Saturday", today.plusDays(2).toString(), list = "L1", priority = 5),
        TaskItem("later", "Later this month", today.plusDays(20).toString(), list = "L1"),
        TaskItem("dec", "December", "2026-12-03", list = "L1"),
        TaskItem("none", "No date", null, list = "L2", categories = listOf("phone")),
        TaskItem("kid", "Child", null, list = "L2", parent = "none"),
        TaskItem("grandkid", "Grandchild", null, list = "L2", parent = "kid", status = "completed"),
        TaskItem("done", "Done today", null, list = "L1", status = "completed", completedAt = now.minusHours(1).toInstant().toString()),
        TaskItem("doneOld", "Done long ago", null, list = "L1", status = "completed", completedAt = now.minusDays(30).toInstant().toString()),
    )

    private fun build(v: TaskView, o: GroupOptions = GroupOptions(lists = lists)) = TaskGroups.build(v, tasks, nowMs, zone, o)
    private fun ids(v: TaskView, o: GroupOptions = GroupOptions(lists = lists)) =
        build(v, o).associate { g -> g.id to g.entries.map { it.task.uid } }

    /** Today: overdue first (oldest first), then today, then started; deferred tasks left out. */
    @Test fun today() {
        val g = ids(TaskView.TODAY)
        assertEquals(listOf("overdue", "today", "started"), g.keys.toList())
        assertEquals(listOf("old", "late"), g["overdue"])
        assertEquals(listOf("today", "todayDate"), g["today"])
        assertEquals(listOf("started"), g["started"])
        assertTrue(build(TaskView.TODAY).first().emphasis)
        val deferred = ids(TaskView.TODAY, GroupOptions(lists = lists, showDeferred = true))
        assertTrue("deferred" in deferred["today"]!!)
    }

    /** Upcoming: day groups for 7 days ("+ Add to" day), then later this month, then months; deferred tasks on their start day. */
    @Test fun upcoming() {
        val groups = build(TaskView.SmartView(Smart.UPCOMING))
        assertEquals(listOf("Tomorrow", "Sat 3 Oct", "Later this month", "December"), groups.map { it.title })
        assertEquals(today.plusDays(1), groups[0].day)
        assertEquals(setOf("sat", "deferred"), groups[1].entries.map { it.task.uid }.toSet())
        assertNull(groups[2].day)
    }

    @Test fun otherSmartLists() {
        assertEquals(mapOf("l-L2" to listOf("none")), ids(TaskView.SmartView(Smart.NODATE)))
        assertEquals(listOf("today"), ids(TaskView.SmartView(Smart.HIGH))["high"])
        assertEquals(listOf("tmrw"), ids(TaskView.SmartView(Smart.EMAIL))["email"])
        val scheduled = build(TaskView.SmartView(Smart.SCHEDULED))
        assertTrue(scheduled.first().emphasis)
        assertEquals("Today", scheduled.first { it.day == today }.title)
        val all = ids(TaskView.SmartView(Smart.ALL))
        assertEquals(listOf("l-L1", "l-L2"), all.keys.toList())
        assertFalse("deferred" in all.values.flatten())
        val completed = ids(TaskView.SmartView(Smart.COMPLETED))
        assertEquals(listOf("done"), completed["today"])
        assertEquals("doneOld", completed["earlier"]!!.first())
    }

    /** A list: subtasks under their parent; a deeper level is flat and marked nested; completed ones count in n/m. */
    @Test fun listViewSubtasks() {
        val e = build(TaskView.ListView("L2")).single().entries
        val none = e.first { it.task.uid == "none" }
        assertEquals(listOf("kid", "grandkid"), none.subtasks.map { it.task.uid })
        assertEquals(listOf(false, true), none.subtasks.map { it.nested })
        assertEquals(1 to 2, none.doneCount to none.subtasks.size)
        assertEquals(listOf("kid"), none.shown.map { it.task.uid })
        assertTrue(e.none { it.task.uid == "kid" })
        val withDone = build(TaskView.ListView("L2"), GroupOptions(lists = lists, showCompleted = true)).first().entries.first { it.task.uid == "none" }
        assertEquals(2, withDone.shown.size)
        assertEquals(listOf("none"), ids(TaskView.TagView("phone"))["tag"])
    }

    @Test fun counts() {
        val c = TaskGroups.counts(tasks, nowMs, zone)
        assertEquals(5, c["today"])
        assertEquals(2, c["overdue"])
        assertEquals(1, c["high"])
        assertEquals(1, c["email"])
    }

    /** The due chip: overdue and within two days are the loud tones; deferred says when it starts. */
    @Test fun dueChip() {
        fun chip(uid: String) = DueChip.of(tasks.first { it.uid == uid }, nowMs, zone)!!
        assertEquals(DueChip("Overdue 3d", DueChip.Tone.OVERDUE), chip("old"))
        assertEquals(DueChip("Overdue 2h", DueChip.Tone.OVERDUE), chip("late"))
        assertEquals(DueChip.Tone.SOON, chip("today").tone)
        assertTrue(chip("today").text.startsWith("Today "))
        assertEquals(DueChip("Tomorrow", DueChip.Tone.SOON), chip("tmrw"))
        assertEquals(DueChip("Sat", DueChip.Tone.SOON), chip("sat"))
        assertEquals(DueChip("21 Oct", DueChip.Tone.NORMAL), chip("later"))
        assertEquals(DueChip("Starts Sat", DueChip.Tone.DEFERRED), chip("deferred"))
        assertNull(DueChip.of(tasks.first { it.uid == "none" }, nowMs, zone))
    }

    /** task.search: free text plus list:, tag:/@, is:done, is:overdue, has:email, pri:high, due:<. */
    @Test fun search() {
        fun q(s: String) = TaskSearch.filter(tasks, s, lists, nowMs, zone).map { it.uid }.toSet()
        assertEquals(setOf("late"), q("report"))
        assertEquals(setOf("none"), q("@phone"))
        assertEquals(setOf("none"), q("tag:phone"))
        assertEquals(setOf("done", "doneOld", "grandkid"), q("is:done"))
        assertEquals(setOf("late", "old"), q("is:overdue"))
        assertEquals(setOf("tmrw"), q("has:email"))
        assertEquals(setOf("today"), q("pri:high"))
        assertEquals(setOf("today", "todayDate", "tmrw", "none", "kid"), q("list:home"))
        assertEquals(setOf("late", "old", "today", "todayDate", "deferred"), q("due:<fri"))
    }

    /** task.create: the grammar's parse becomes the draft; the view's prefill fills only what the line leaves out. */
    @Test fun quickAddToDraft() {
        val r = QuickTask.parse("Pay rent every month on the 1st !! #Work @bills", now, lists)
        val d = QuickTask.plan(r, "u1", zone, QuickPrefill(day = today, tag = "home")).draft
        assertEquals("Pay rent", d.summary)
        assertEquals("2026-11-01", d.due)
        assertEquals("FREQ=MONTHLY;BYMONTHDAY=1", d.recurrence)
        assertEquals("due", d.repeatFrom)
        assertEquals(5, d.priority)
        assertEquals("L1", d.list)
        assertEquals(listOf("bills", "home"), d.categories)
        // Reminders: relative ones as given; an absolute one ("remind at 9am") as minutes before the due.
        assertEquals(listOf(60), QuickTask.plan(QuickTask.parse("Submit report fri 5pm remind 1h before", now, lists), "r1", zone).draft.reminders)
        assertEquals(listOf(-540), QuickTask.plan(QuickTask.parse("Pay bill fri remind at 9am", now, lists), "r2", zone).draft.reminders)
        assertEquals("FREQ=DAILY;INTERVAL=3" to "completion",
            QuickTask.plan(QuickTask.parse("Water plants every! 3 days", now, lists), "r3", zone).draft.let { it.recurrence to it.repeatFrom })

        // Prefill: Today's view gives a date-only due today; High priority gives priority 1.
        val plain = QuickTask.plan(QuickTask.parse("Water plants", now, lists), "u2", zone, QuickPrefill.of(TaskView.TODAY, today)).draft
        assertEquals(today.toString(), plain.due)
        val high = QuickTask.plan(QuickTask.parse("Call Dana fri 3pm", now, lists), "u3", zone,
            QuickPrefill.of(TaskView.SmartView(Smart.HIGH), today)).draft
        assertEquals(1, high.priority)
        assertEquals(LocalDate.of(2026, 10, 2), java.time.OffsetDateTime.parse(high.due!!).toLocalDate())
        assertEquals(zone.id, high.tz)

        // An unknown #name asks for a new list.
        val fresh = QuickTask.plan(QuickTask.parse("Buy milk #Errands", now, lists), "u4", zone)
        assertEquals("Errands", fresh.createList)
        assertNull(fresh.draft.list)
    }

    /** The quick-add line's "keep as text": × on a chip puts the words back in the title and the parse skips them. */
    @Test fun keepAsText() {
        val r = QuickTask.parse("Read the book friday", now, lists)
        val date = r.tokens.first { it.kind == TokenKind.DATE }
        val kept = QuickTask.parse("Read the book friday", now, lists, QuickTask.keepAsText(emptySet(), date))
        assertEquals("Read the book friday", kept.title)
        assertNull(kept.due)
    }

    /**
     * The editor's new fields (android.editor): task.set_start, task.set_repeat ("from completion"),
     * task.tag and task.remind_at ("At a time…", kept as minutes before the due). An unchanged
     * rule is not sent, so a rule mailr cannot edit is never rewritten.
     */
    @Test fun editorDraft() {
        val orig = TaskItem("e1", "Water plants", "2026-10-02", recurring = true, rrule = "FREQ=WEEKLY;BYSETPOS=1;BYDAY=MO", categories = listOf("home"))
        val s = io.github.murtazanasir.mailr.ui.calendar.TaskEditorState(orig, null, null, "e1")
        assertNull(s.draft().recurrence)
        s.setStart(LocalDate.of(2026, 10, 1))
        s.recurrence = io.github.murtazanasir.mailr.ui.calendar.Recurrence(io.github.murtazanasir.mailr.ui.calendar.Freq.WEEKLY, byDay = setOf(java.time.DayOfWeek.FRIDAY))
        s.repeatFrom = "completion"
        s.addTag("@garden")
        s.addReminderAt(java.time.LocalDateTime.of(2026, 10, 1, 18, 0))
        val d = s.draft()
        assertEquals("2026-10-01", d.start)
        assertEquals("FREQ=WEEKLY;BYDAY=FR", d.recurrence)
        assertEquals("completion", d.repeatFrom)
        assertEquals(listOf("home", "garden"), d.categories)
        // "At a time…" is an absolute reminder (task.remind_at, reminders_at), not an offset.
        assertEquals(emptyList<Int>(), d.reminders)
        assertEquals(listOf(java.time.LocalDateTime.of(2026, 10, 1, 18, 0).atZone(java.time.ZoneId.systemDefault()).toInstant().toString()), d.remindersAt)
        s.recurrence = null
        s.setStart(null)
        assertEquals("", s.draft().recurrence)
        assertTrue(s.draft().clearStart || orig.start == null)
    }

    /** android.share: the shared text is the title; a link goes to the notes. */
    @Test fun shareBecomesATask() {
        val link = ShareTask.draft(SharedContent(subject = "Kotlin 2.1 release notes", text = "https://kotlinlang.org/docs/whatsnew21.html"), "s1")
        assertEquals("Kotlin 2.1 release notes", link.summary)
        assertEquals("https://kotlinlang.org/docs/whatsnew21.html", link.notes)
        val text = ShareTask.draft(SharedContent(text = "Book the dentist\nhttps://example.com/booking"), "s2")
        assertEquals("Book the dentist", text.summary)
        assertEquals("https://example.com/booking", text.notes)
        val bare = ShareTask.draft(SharedContent(text = "https://example.com/a/b"), "s3")
        assertEquals("example.com/a/b", bare.summary)
    }
}
