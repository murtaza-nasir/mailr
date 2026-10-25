// ACT-SURFACES: android.notification
package io.github.murtazanasir.mailr

import android.Manifest
import android.app.Application
import android.app.Notification
import android.app.NotificationManager
import android.app.PendingIntent
import android.content.Intent
import io.github.murtazanasir.mailr.data.reminders.Kind
import io.github.murtazanasir.mailr.data.reminders.ReminderActionReceiver
import io.github.murtazanasir.mailr.data.reminders.ReminderChannels
import io.github.murtazanasir.mailr.data.reminders.ReminderNotifier
import io.github.murtazanasir.mailr.data.reminders.RState
import io.github.murtazanasir.mailr.data.reminders.SummarySpec
import io.github.murtazanasir.mailr.ui.calendar.ReminderActivity
import kotlinx.coroutines.runBlocking
import kotlinx.serialization.json.boolean
import kotlinx.serialization.json.jsonPrimitive
import org.junit.After
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNotNull
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Before
import org.junit.Test
import org.junit.runner.RunWith
import org.robolectric.RobolectricTestRunner
import org.robolectric.Shadows.shadowOf
import org.robolectric.annotation.Config

/**
 * Reminder notifications: a tap opens the sheet
 * (notification.open) or, on the group summary, the triage queue (notification.open_stack) and
 * ends nothing; Done on a task reminder completes the task (task.complete on android.notification).
 */
@RunWith(RobolectricTestRunner::class)
@Config(application = Application::class, sdk = [34])
class ReminderNotificationTest {
    private val h = CalendarHarness()
    private lateinit var nm: NotificationManager
    private lateinit var notifier: ReminderNotifier

    @Before fun setUp() {
        h.start()
        shadowOf(h.app).grantPermissions(Manifest.permission.POST_NOTIFICATIONS)
        ReminderChannels.create(h.app)
        nm = h.app.getSystemService(NotificationManager::class.java)
        notifier = ReminderNotifier(h.app)
    }

    @After fun tearDown() = h.stop()

    private fun posted(tag: String, kind: String): Notification? =
        shadowOf(nm).getNotification(tag, if (kind == Kind.EVENT) ReminderNotifier.EVENT_ID else ReminderNotifier.TASK_ID)

    private fun intentOf(pi: PendingIntent): Intent = shadowOf(pi).savedIntent

    /** notification.open: a tap opens this reminder's sheet; the tap itself neither cancels nor handles it. */
    @Test fun tapOpensTheSheetAndEndsNothing() {
        notifier.post(h.taskReminder, alert = false)
        val n = posted(h.taskReminder.key, Kind.TASK)!!
        assertEquals(0, n.flags and Notification.FLAG_AUTO_CANCEL)
        assertTrue(shadowOf(n.contentIntent).isActivityIntent)
        val open = intentOf(n.contentIntent)
        assertEquals(ReminderActivity::class.java.name, open.component!!.className)
        assertEquals(h.taskReminder.key, open.getStringExtra(ReminderActivity.EXTRA_KEY))
        assertFalse(open.getBooleanExtra(ReminderActivity.EXTRA_STACK, true))
        // The sheet loads the reminder without changing it.
        assertEquals(RState.SHOWN, h.reminder(h.taskReminder.key)!!.state)
        assertTrue(h.queued().isEmpty())
    }

    /** notification.open_stack: the summary of stacked reminders opens the triage queue over all of them. */
    @Test fun summaryOpensTheTriageQueue() {
        notifier.postSummary(SummarySpec(Kind.TASK, 3, "3 task reminders", listOf("a", "b", "c"), 0))
        val n = posted("summary-${Kind.TASK}", Kind.TASK)!!
        assertEquals(0, n.flags and Notification.FLAG_AUTO_CANCEL)
        val open = intentOf(n.contentIntent)
        assertEquals(ReminderActivity::class.java.name, open.component!!.className)
        assertTrue(open.getBooleanExtra(ReminderActivity.EXTRA_STACK, false))
        assertNull(open.getStringExtra(ReminderActivity.EXTRA_KEY))
        // The queue's order: missed first, then the oldest (ReminderEngine.shownQueue).
        val missed = h.eventReminder.copy(missed = true, fireAt = h.eventReminder.fireAt + 60_000)
        runBlocking { h.g.db.reminders().put(listOf(missed)) }
        assertEquals(listOf(missed.key, h.taskReminder.key), runBlocking { h.g.reminders.shownQueue() }.map { it.key })
    }

    /** task.complete: the task reminder's Done button completes the task (queued, offline-safe) and removes it. */
    @Test fun doneCompletesTheTask() {
        notifier.post(h.taskReminder, alert = false)
        val n = posted(h.taskReminder.key, Kind.TASK)!!
        assertEquals(listOf("Done", "Snooze 1 h", "Snooze…"), n.actions.map { it.title.toString() })
        val done = intentOf(n.actions.first().actionIntent)
        assertTrue(shadowOf(n.actions.first().actionIntent).isBroadcastIntent)
        assertEquals(ReminderActionReceiver.DONE, done.action)
        assertEquals(h.taskReminder.key, done.getStringExtra("key"))
        // What the receiver runs for DONE.
        runBlocking { h.g.reminders.done(h.taskReminder.key) }
        assertEquals(true, h.queuedBody("PATCH", "tasks/t1")["completed"]!!.jsonPrimitive.boolean)
        assertNull(posted(h.taskReminder.key, Kind.TASK))
        assertNotNull(h.storedTask("t1")?.takeIf { it.completed })
    }
}
