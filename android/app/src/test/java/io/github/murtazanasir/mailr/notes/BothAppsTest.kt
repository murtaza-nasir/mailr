package io.github.murtazanasir.mailr.notes

import android.app.Application
import android.appwidget.AppWidgetManager
import android.content.Intent
import android.net.Uri
import androidx.test.core.app.ApplicationProvider
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test
import org.junit.runner.RunWith
import org.robolectric.RobolectricTestRunner
import org.robolectric.annotation.Config

/**
 * Both apps on one phone: the mailr app has no notes share target and no notes widget
 * (they are the Notes app's), so each entry shows once; it opens notes for the Notes app's "Open in
 * mailr" (mailr://notes/…, mailr://tables/…); its database is its own file.
 */
@RunWith(RobolectricTestRunner::class)
@Config(application = Application::class, sdk = [34])
class BothAppsTest {
    private val ctx get() = ApplicationProvider.getApplicationContext<Application>()

    @Test fun theMailrAppHasNoNotesShareTargetOrWidget() {
        val pm = ctx.packageManager
        val shares = pm.queryIntentActivities(Intent(Intent.ACTION_SEND).setType("text/plain").setPackage(ctx.packageName), 0)
        assertTrue(shares.isNotEmpty())
        assertTrue(shares.map { it.activityInfo.name }.toString(), shares.none { "notes" in it.activityInfo.name.lowercase() })
        val widgets = pm.queryBroadcastReceivers(Intent(AppWidgetManager.ACTION_APPWIDGET_UPDATE).setPackage(ctx.packageName), 0)
        assertEquals(listOf("AgendaWidgetReceiver", "AskWidgetReceiver", "TodayWidgetReceiver"), widgets.map { it.activityInfo.name.substringAfterLast('.') }.sorted())
    }

    @Test fun theMailrAppOpensNotesForTheNotesApp() {
        for (link in listOf("mailr://notes/n/n12", "mailr://tables/t/t3")) {
            val hits = ctx.packageManager.queryIntentActivities(Intent(Intent.ACTION_VIEW, Uri.parse(link)).setPackage(ctx.packageName), 0)
            assertTrue(link, hits.any { it.activityInfo.name.endsWith(".ui.MainActivity") })
        }
    }

    @Test fun eachAppHasItsOwnIds() {
        assertTrue(NotesApp.APPLICATION_ID != ctx.packageName && NotesApp.DEBUG_APPLICATION_ID != ctx.packageName)
    }
}
