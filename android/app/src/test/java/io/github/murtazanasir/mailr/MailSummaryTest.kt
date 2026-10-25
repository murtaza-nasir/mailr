package io.github.murtazanasir.mailr

import android.Manifest
import android.app.Application
import android.app.Notification
import android.app.NotificationManager
import androidx.test.core.app.ApplicationProvider
import io.github.murtazanasir.mailr.data.sync.Channels
import io.github.murtazanasir.mailr.data.sync.MailNotifications
import io.github.murtazanasir.mailr.data.sync.Notifier
import org.junit.After
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Before
import org.junit.Test
import org.junit.runner.RunWith
import org.robolectric.RobolectricTestRunner
import org.robolectric.Shadows.shadowOf
import org.robolectric.annotation.Config

/**
 * New-mail notifications are one group with a summary ("2 new conversations", one line each), also
 * when the mail came one push at a time; the summary follows when one is acted on.
 */
@RunWith(RobolectricTestRunner::class)
@Config(application = Application::class, sdk = [34])
class MailSummaryTest {
    private val app get() = ApplicationProvider.getApplicationContext<Application>()
    private fun nm() = app.getSystemService(NotificationManager::class.java)
    private fun summary() = nm().activeNotifications.singleOrNull { it.tag == MailNotifications.SUMMARY_TAG }?.notification

    @Before fun setUp() {
        shadowOf(app).grantPermissions(Manifest.permission.POST_NOTIFICATIONS)
        Channels.create(app)
    }

    @After fun tearDown() = nm().cancelAll()

    @Test fun mailArrivingOneAtATimeIsGroupedWithASummary() {
        val (a, b, c) = Fixtures.conversations.take(3)
        Notifier.newMail(app, listOf(a))
        assertNull("one conversation: no summary", summary())
        Notifier.newMail(app, listOf(b))
        val s = summary()!!
        assertEquals("2 new conversations", s.extras.getCharSequence(Notification.EXTRA_TITLE).toString())
        assertTrue(s.flags and Notification.FLAG_GROUP_SUMMARY != 0)
        assertEquals("mail", s.group)
        assertEquals(2, s.extras.getCharSequenceArray(Notification.EXTRA_TEXT_LINES)!!.size)
        // A push hint is grouped too.
        Notifier.newMailHint(app, c.id, "Super.com")
        assertEquals("3 new conversations", summary()!!.extras.getCharSequence(Notification.EXTRA_TITLE).toString())
        // Acted on: the count follows, and under two the summary goes.
        MailNotifications.cancel(app, listOf(a.id))
        assertEquals("2 new conversations", summary()!!.extras.getCharSequence(Notification.EXTRA_TITLE).toString())
        MailNotifications.cancel(app, listOf(b.id))
        assertNull(summary())
    }
}
