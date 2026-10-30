// ACT-SURFACES: android.notification android.sheet android.triage android.fullscreen
package io.github.murtazanasir.mailr

import android.Manifest
import android.app.Application
import android.app.Notification
import android.app.NotificationManager
import android.content.Intent
import androidx.compose.material3.MaterialTheme
import io.github.murtazanasir.mailr.testing.createComposeRule
import androidx.compose.ui.test.onNodeWithContentDescription
import androidx.compose.ui.test.onNodeWithText
import androidx.compose.ui.test.performClick
import io.github.murtazanasir.mailr.data.agent.AskAbout
import io.github.murtazanasir.mailr.data.reminders.Agenda
import io.github.murtazanasir.mailr.data.reminders.AlertStyle
import io.github.murtazanasir.mailr.data.reminders.Kind
import io.github.murtazanasir.mailr.data.reminders.RState
import io.github.murtazanasir.mailr.data.reminders.ReminderChannels
import io.github.murtazanasir.mailr.data.reminders.ReminderNotifier
import io.github.murtazanasir.mailr.ui.ask.AskActivity
import io.github.murtazanasir.mailr.ui.ask.AskIntents
import io.github.murtazanasir.mailr.ui.calendar.AlertCallbacks
import io.github.murtazanasir.mailr.ui.calendar.LockedAlert
import androidx.compose.ui.test.assertCountEquals
import androidx.compose.ui.test.onAllNodesWithText
import io.github.murtazanasir.mailr.ui.calendar.QuickActionCard
import io.github.murtazanasir.mailr.ui.calendar.QuickActions
import io.github.murtazanasir.mailr.ui.calendar.ReminderAlertActivity
import io.github.murtazanasir.mailr.ui.calendar.ReminderAlertScreen
import io.github.murtazanasir.mailr.ui.calendar.SheetTarget
import org.junit.After
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNotNull
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Before
import org.junit.Rule
import org.junit.Test
import org.junit.runner.RunWith
import org.robolectric.RobolectricTestRunner
import org.robolectric.Shadows.shadowOf
import org.robolectric.annotation.Config

/**
 * Reminder alerts on the screen: the notification's Ask button (agent.ask_about on
 * android.notification) opens the capture with the item and handles nothing; the full-screen intent
 * (reminder.full_screen) only when asked for and allowed; a repeat sounds again; the agenda
 * (reminder.agenda) is quiet; the sheet's microphone (agent.ask_about on android.sheet); the
 * full-screen alert's buttons (android.fullscreen: task.complete, task.snooze, event.snooze,
 * event.join, reminder.dismiss, agent.ask_about), and Close leaves the reminder showing.
 */
@RunWith(RobolectricTestRunner::class)
// SDK 33: on 34 a full-screen intent needs a permission Robolectric cannot grant (canUseFullScreenIntent is false).
@Config(application = Application::class, sdk = [33], qualifiers = "w411dp-h891dp")
class ReminderAlertUiTest {
    @get:Rule val compose = createComposeRule()
    private val h = CalendarHarness()
    private lateinit var nm: NotificationManager
    private lateinit var notifier: ReminderNotifier

    @Before fun setUp() {
        h.start()
        shadowOf(h.app).grantPermissions(Manifest.permission.POST_NOTIFICATIONS, Manifest.permission.USE_FULL_SCREEN_INTENT)
        ReminderChannels.create(h.app)
        nm = h.app.getSystemService(NotificationManager::class.java)
        notifier = ReminderNotifier(h.app)
    }

    @After fun tearDown() = h.stop()

    private fun posted(tag: String, kind: String): Notification =
        shadowOf(nm).getNotification(tag, if (kind == Kind.EVENT) ReminderNotifier.EVENT_ID else ReminderNotifier.TASK_ID)!!

    private fun titles(n: Notification) = n.actions.map { it.title.toString() }

    /** agent.ask_about on android.notification: Ask takes the picker's place and opens the capture with the task. */
    @Test fun askButtonOpensTheCaptureWithTheItem() {
        notifier.post(h.taskReminder, alert = true, join = null, style = AlertStyle(ask = true))
        val n = posted(h.taskReminder.key, Kind.TASK)
        assertEquals(listOf("Done", "Snooze 1 h", "Ask Ambrose"), titles(n))
        val pi = n.actions.last().actionIntent
        assertTrue(shadowOf(pi).isActivityIntent)
        val i: Intent = shadowOf(pi).savedIntent
        assertEquals(AskActivity::class.java.name, i.component!!.className)
        assertEquals(AskAbout.Task("t1", h.task.summary), AskIntents.read(i))
        // The notification itself is untouched: the reminder stays until an action ends it.
        assertEquals(RState.SHOWN, h.reminder(h.taskReminder.key)!!.state)

        notifier.post(h.eventReminder, alert = true, join = null, style = AlertStyle(ask = true))
        val e = posted(h.eventReminder.key, Kind.EVENT)
        assertEquals(listOf("Snooze 5 min", "Dismiss", "Ask Ambrose"), titles(e))
        assertEquals(AskAbout.Event("e1", h.event.occurrence, h.event.summary), AskIntents.read(shadowOf(e.actions.last().actionIntent).savedIntent))
    }

    @Test fun withoutAskThePickerButtonsStay() {
        notifier.post(h.taskReminder, alert = true, join = null, style = AlertStyle(ask = false))
        assertEquals(listOf("Done", "Snooze 1 h", "Snooze…"), titles(posted(h.taskReminder.key, Kind.TASK)))
    }

    /** reminder.full_screen: the full-screen intent only when the style asks for it; it opens the alert, not an action. */
    @Test fun fullScreenIntentOnlyWhenAsked() {
        notifier.post(h.taskReminder, alert = true, join = null, style = AlertStyle())
        assertNull(posted(h.taskReminder.key, Kind.TASK).fullScreenIntent)
        notifier.post(h.taskReminder, alert = true, join = null, style = AlertStyle(fullScreen = true))
        val n = posted(h.taskReminder.key, Kind.TASK)
        assertNotNull(n.fullScreenIntent)
        val i = shadowOf(n.fullScreenIntent).savedIntent
        assertEquals(ReminderAlertActivity::class.java.name, i.component!!.className)
        assertEquals(h.taskReminder.key, i.getStringExtra(ReminderAlertActivity.EXTRA_KEY))
        assertEquals(0, n.flags and Notification.FLAG_AUTO_CANCEL)
        // A quiet re-post (alert = false) never takes the screen.
        notifier.post(h.taskReminder, alert = false, join = null, style = AlertStyle(fullScreen = true))
        assertNull(posted(h.taskReminder.key, Kind.TASK).fullScreenIntent)
    }

    /** Do Not Disturb holds the full-screen alert back; the reminder still posts. */
    @Test fun doNotDisturbHoldsTheFullScreenBack() {
        shadowOf(nm).setNotificationPolicyAccessGranted(true)
        nm.setInterruptionFilter(NotificationManager.INTERRUPTION_FILTER_PRIORITY)
        notifier.post(h.taskReminder, alert = true, join = null, style = AlertStyle(fullScreen = true))
        assertNull(posted(h.taskReminder.key, Kind.TASK).fullScreenIntent)
    }

    /** reminder.repeat: a repeat alerts again (only-alert-once is off for it). */
    @Test fun repeatSoundsAgain() {
        notifier.post(h.taskReminder, alert = true, join = null, style = AlertStyle())
        assertTrue(posted(h.taskReminder.key, Kind.TASK).flags and Notification.FLAG_ONLY_ALERT_ONCE != 0)
        notifier.post(h.taskReminder, alert = true, join = null, style = AlertStyle(again = true))
        assertEquals(0, posted(h.taskReminder.key, Kind.TASK).flags and Notification.FLAG_ONLY_ALERT_ONCE)
    }

    /** reminder.agenda: one quiet notification on its own channel; the lock screen sees counts only. */
    @Test fun agendaIsQuiet() {
        notifier.postAgenda(Agenda("Today: 1 event, 1 task", listOf("10:00 AM  Standup", "Due  Send the report"), 0, 1, 1))
        val n = shadowOf(nm).getNotification("agenda", ReminderNotifier.AGENDA_ID)!!
        assertEquals(ReminderChannels.AGENDA, n.channelId)
        assertEquals(NotificationManager.IMPORTANCE_LOW, nm.getNotificationChannel(ReminderChannels.AGENDA).importance)
        assertEquals("Today's agenda", n.publicVersion.extras.getString(Notification.EXTRA_TITLE))
        assertFalse(n.publicVersion.extras.getCharSequence(Notification.EXTRA_TEXT).toString().contains("Standup"))
    }

    /** agent.ask_about on android.sheet: the header's microphone opens the capture with the item; nothing is handled. */
    @Test fun sheetMicrophoneOpensTheCapture() {
        val actions = QuickActions(h.g, h.app)
        compose.setContent { MaterialTheme { QuickActionCard(SheetTarget(h.taskReminder, task = h.task), actions, 9, null, run = {}) } }
        compose.onNodeWithContentDescription("Ask Ambrose about this").performClick()
        compose.waitForIdle()
        val i = h.nextOpened()!!
        assertEquals(AskActivity::class.java.name, i.component!!.className)
        assertEquals(AskAbout.Task("t1", h.task.summary), AskIntents.read(i))
        assertEquals(RState.SHOWN, h.reminder(h.taskReminder.key)!!.state)
        assertTrue(h.queued().isEmpty())
    }

    /** android.fullscreen: each button calls its action; Close calls only close. */
    @Test fun fullScreenButtons() {
        val calls = mutableListOf<String>()
        val cb = AlertCallbacks(done = { calls += "done" }, snooze = { calls += "snooze:$it" }, dismiss = { calls += "dismiss" },
            join = { calls += "join:$it" }, more = { calls += "more" }, ask = { calls += "ask" }, close = { calls += "close" })
        val now = h.now.toInstant().toEpochMilli()
        val task = h.taskReminder.copy(startAt = now + 60_000L)
        val showTask = androidx.compose.runtime.mutableStateOf(true)
        compose.setContent {
            MaterialTheme {
                if (showTask.value) ReminderAlertScreen(task, null, now, cb)
                else ReminderAlertScreen(h.eventReminder, "https://meet.example.com/abc", now, cb)
            }
        }
        compose.onNodeWithText("Due in 1 min").assertExists()
        compose.onNodeWithText("Done").performClick()
        compose.onNodeWithText("Snooze 10 min").performClick()
        compose.onNodeWithText("Ask Ambrose").performClick()
        compose.onNodeWithText("Dismiss").performClick()
        compose.onNodeWithText("More choices").performClick()
        compose.onNodeWithContentDescription("Close; the reminder stays").performClick()
        assertEquals(listOf("done", "snooze:10m", "ask", "dismiss", "more", "close"), calls)
        calls.clear()
        androidx.compose.runtime.snapshots.Snapshot.withMutableSnapshot { showTask.value = false }
        compose.waitForIdle()
        compose.onNodeWithText("Starts in 40 min").assertExists()
        compose.onNodeWithText("Join").performClick()
        compose.onNodeWithText("Snooze 5 min").performClick()
        assertEquals(listOf("join:https://meet.example.com/abc", "snooze:5m"), calls)
    }

    /** On a locked phone the alert names only the kind of reminder: no title, no actions; Unlock asks for the phone's lock. */
    @Test fun aLockedPhoneShowsNoDetailsAndNoActions() {
        var unlocks = 0
        var closed = false
        compose.setContent {
            MaterialTheme { LockedAlert(event = false, now = 0L, unlock = { unlocks++ }, close = { closed = true }) }
        }
        compose.onNodeWithText("Task reminder").assertExists()
        compose.onNodeWithText("Unlock your phone to see this reminder").assertExists()
        for (action in listOf("Done", "Dismiss", "More choices")) compose.onAllNodesWithText(action).assertCountEquals(0)
        compose.onAllNodesWithText("Snooze", substring = true).assertCountEquals(0)
        compose.onNodeWithText("Unlock").performClick()
        assertEquals(1, unlocks)
        compose.onNodeWithContentDescription("Close; the reminder stays").performClick()
        assertTrue(closed)
    }
}
