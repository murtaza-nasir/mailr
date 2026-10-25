package io.github.murtazanasir.mailr

import android.app.Application
import android.appwidget.AppWidgetManager
import android.content.Intent
import android.content.pm.PackageManager
import androidx.test.core.app.ApplicationProvider
import io.github.murtazanasir.mailr.auth.HandoverActivity
import io.github.murtazanasir.mailr.data.auth.Handover
import io.github.murtazanasir.mailr.ui.MainActivity
import io.github.murtazanasir.mailr.ui.agenda.AgendaWidgetReceiver
import io.github.murtazanasir.mailr.ui.ask.AskTileService
import io.github.murtazanasir.mailr.ui.ask.AskWidgetReceiver
import io.github.murtazanasir.mailr.ui.ask.NewTaskTileService
import io.github.murtazanasir.mailr.ui.today.TodayWidgetReceiver
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test
import org.junit.runner.RunWith
import org.robolectric.RobolectricTestRunner
import org.robolectric.annotation.Config

/**
 * The manifest names the classes themselves: the launcher entry, the widgets, the tiles and the
 * handover the Notes app starts, with no aliases, and every component of the app's own is in its
 * Kotlin package.
 */
@RunWith(RobolectricTestRunner::class)
@Config(application = Application::class, sdk = [34])
class ComponentNamesTest {
    private val ctx get() = ApplicationProvider.getApplicationContext<Application>()

    @Test fun theLauncherEntryIsTheMainActivity() {
        val hits = ctx.packageManager.queryIntentActivities(
            Intent(Intent.ACTION_MAIN).addCategory(Intent.CATEGORY_LAUNCHER).setPackage(ctx.packageName), 0)
        assertEquals(listOf(MainActivity::class.java.name), hits.map { it.activityInfo.name })
        assertNull(hits.single().activityInfo.targetActivity)
    }

    @Test fun theWidgetsTilesAndHandoverAreTheirClasses() {
        val pm = ctx.packageManager
        val widgets = pm.queryBroadcastReceivers(Intent(AppWidgetManager.ACTION_APPWIDGET_UPDATE).setPackage(ctx.packageName), 0)
            .map { it.activityInfo.name }.sorted()
        assertEquals(listOf(AgendaWidgetReceiver::class, AskWidgetReceiver::class, TodayWidgetReceiver::class).map { it.java.name }, widgets)
        val tiles = pm.queryIntentServices(Intent("android.service.quicksettings.action.QS_TILE").setPackage(ctx.packageName), 0)
            .map { it.serviceInfo.name }.sorted()
        assertEquals(listOf(AskTileService::class, NewTaskTileService::class).map { it.java.name }, tiles)
        assertEquals(HandoverActivity::class.java.name, Handover.ACTIVITY)
    }

    @Test fun everyOwnComponentIsInTheAppsPackage() {
        val flags = PackageManager.GET_ACTIVITIES or PackageManager.GET_RECEIVERS or PackageManager.GET_SERVICES or
            PackageManager.GET_PROVIDERS or PackageManager.MATCH_DISABLED_COMPONENTS
        val info = ctx.packageManager.getPackageInfo(ctx.packageName, flags)
        val names = listOfNotNull(info.activities, info.receivers, info.services, info.providers).flatMap { c -> c.map { it.name } }
        val own = names.filter { it.contains(".mailr.") }
        assertTrue(own.isNotEmpty())
        assertEquals(emptyList<String>(), own.filterNot { it.startsWith("io.github.murtazanasir.mailr.") })
        assertEquals(emptyList<String>(), info.activities.orEmpty().mapNotNull { it.targetActivity })
    }
}
