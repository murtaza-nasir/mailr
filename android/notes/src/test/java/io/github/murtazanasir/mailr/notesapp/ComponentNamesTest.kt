package io.github.murtazanasir.mailr.notesapp

import android.app.Application
import android.appwidget.AppWidgetManager
import android.content.Intent
import android.content.pm.PackageManager
import androidx.test.core.app.ApplicationProvider
import io.github.murtazanasir.mailr.notesapp.capture.NewNoteTile
import io.github.murtazanasir.mailr.notesapp.widget.CaptureWidgetReceiver
import io.github.murtazanasir.mailr.notesapp.widget.ChecklistWidgetReceiver
import io.github.murtazanasir.mailr.notesapp.widget.NoteWidgetReceiver
import io.github.murtazanasir.mailr.notesapp.widget.TableWidgetReceiver
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test
import org.junit.runner.RunWith
import org.robolectric.RobolectricTestRunner
import org.robolectric.annotation.Config

/**
 * The manifest names the classes themselves: the launcher entry, the widgets and the tile, with no
 * aliases, and every component of the app's own is in its Kotlin package.
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

    @Test fun theWidgetsAndTheTileAreTheirClasses() {
        val pm = ctx.packageManager
        val widgets = pm.queryBroadcastReceivers(Intent(AppWidgetManager.ACTION_APPWIDGET_UPDATE).setPackage(ctx.packageName), 0)
            .map { it.activityInfo.name }.sorted()
        assertEquals(listOf(CaptureWidgetReceiver::class, ChecklistWidgetReceiver::class, NoteWidgetReceiver::class,
            TableWidgetReceiver::class).map { it.java.name }, widgets)
        val tiles = pm.queryIntentServices(Intent("android.service.quicksettings.action.QS_TILE").setPackage(ctx.packageName), 0)
            .map { it.serviceInfo.name }
        assertEquals(listOf(NewNoteTile::class.java.name), tiles)
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
