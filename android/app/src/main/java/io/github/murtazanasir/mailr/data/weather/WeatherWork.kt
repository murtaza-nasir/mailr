package io.github.murtazanasir.mailr.data.weather

import io.github.murtazanasir.mailr.util.catching

import android.Manifest
import android.content.Context
import android.content.pm.PackageManager
import android.location.Location
import android.location.LocationManager
import android.os.Build
import androidx.core.content.ContextCompat
import androidx.work.Constraints
import androidx.work.CoroutineWorker
import androidx.work.ExistingPeriodicWorkPolicy
import androidx.work.ExistingWorkPolicy
import androidx.work.NetworkType
import androidx.work.OneTimeWorkRequestBuilder
import androidx.work.PeriodicWorkRequestBuilder
import androidx.work.WorkManager
import androidx.work.WorkerParameters
import io.github.murtazanasir.mailr.time.AppClock
import io.github.murtazanasir.mailr.ui.agenda.AgendaWidgets
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.launch
import kotlinx.coroutines.suspendCancellableCoroutine
import kotlinx.coroutines.withTimeoutOrNull
import java.util.concurrent.TimeUnit
import kotlin.coroutines.resume

/**
 * The Agenda widget's weather: one fetch an hour at most (WorkManager, network required), only while
 * an Agenda widget is on the home screen and weather is on. The widget itself never fetches; it draws
 * the last reading.
 */
object WeatherWork {
    private const val PERIODIC = "agenda-weather"
    private const val NOW = "agenda-weather-now"
    /** A reading younger than this is kept: the hourly limit holds even when a fetch is asked for early. */
    const val FRESH_MS = 55 * 60 * 1000L

    private val online = Constraints.Builder().setRequiredNetworkType(NetworkType.CONNECTED).build()

    /** Every hour while a widget is placed and weather is on; cancelled otherwise. */
    fun schedule(context: Context) {
        val wm = WorkManager.getInstance(context)
        if (!WeatherStore(context).enabled) { cancel(context); return }
        wm.enqueueUniquePeriodicWork(PERIODIC, ExistingPeriodicWorkPolicy.KEEP,
            PeriodicWorkRequestBuilder<WeatherWorker>(1, TimeUnit.HOURS).setConstraints(online).build())
    }

    /** A fetch soon (a new place or a widget just placed); a fresh reading for the same place is kept. */
    fun soon(context: Context) {
        if (!WeatherStore(context).enabled) return
        WorkManager.getInstance(context).enqueueUniqueWork(NOW, ExistingWorkPolicy.REPLACE,
            OneTimeWorkRequestBuilder<WeatherWorker>().setConstraints(online).build())
    }

    fun cancel(context: Context) {
        WorkManager.getInstance(context).cancelUniqueWork(PERIODIC)
        WorkManager.getInstance(context).cancelUniqueWork(NOW)
    }

    /**
     * mailr came to the front: with an Agenda widget placed and weather for this phone's location, the
     * last known location is read (no new fix), at most once an hour; a new place fetches soon.
     */
    fun onForeground(context: Context) {
        try {
            val ids = android.appwidget.AppWidgetManager.getInstance(context)
                .getAppWidgetIds(android.content.ComponentName(context, io.github.murtazanasir.mailr.ui.agenda.AgendaWidgetReceiver::class.java))
            if (ids.isEmpty()) return
            val store = WeatherStore(context)
            if (!store.enabled || store.source != WeatherSource.LOCATION) return
            if (AppClock.millis() - store.hereAt in 0 until FRESH_MS && store.here != null) return
            val p = WeatherLocation.lastKnown(context)
            if (p == null) {
                // No last known location: ask for a fresh one while mailr is in front (in the background
                // Android gives none, so the hourly fetch cannot find a place by itself).
                if (store.here == null) locateNow(context)
                return
            }
            val moved = store.here?.let { it.lat != p.lat || it.lon != p.lon } ?: true
            store.here = p
            if (moved) soon(context)
        } catch (_: Exception) {
            // No widget service (tests, a launcher without widgets): nothing to do.
        }
    }

    /** A fresh approximate fix in the app's scope (it outlives the screen that asked), then a fetch. */
    fun locateNow(context: Context) {
        val g = io.github.murtazanasir.mailr.ui.agenda.AgendaWidgets.graph(context) ?: return
        g.scope.launch {
            val p = try { WeatherLocation.current(context) } catch (e: CancellationException) { throw e } catch (_: Exception) { null }
            if (p != null) {
                WeatherStore(context).here = p
                soon(context)
            }
        }
    }

    /** True when [r] is still fresh for [p]: no fetch needed. */
    fun fresh(r: WeatherReading?, p: WeatherPlace, now: Long): Boolean =
        r != null && r.place.lat == p.lat && r.place.lon == p.lon && now - r.fetchedAt in 0 until FRESH_MS

    /** One run: the place, then the forecast unless the last one is fresh; the widgets are drawn again. */
    suspend fun run(context: Context, api: OpenMeteo = OpenMeteo()): Boolean {
        val store = WeatherStore(context)
        if (!store.enabled) return true
        if (store.source == WeatherSource.LOCATION) WeatherLocation.lastKnown(context)?.let { store.here = it }
        val p = store.place ?: return true
        if (fresh(store.reading, p, AppClock.millis())) return true
        store.reading = api.forecast(p)
        AgendaWidgets.refresh(context)
        return true
    }
}

class WeatherWorker(ctx: Context, params: WorkerParameters) : CoroutineWorker(ctx, params) {
    override suspend fun doWork(): Result = try {
        WeatherWork.run(applicationContext)
        Result.success()
    } catch (e: CancellationException) {
        throw e
    } catch (_: Exception) {
        // The next hourly run tries again; the widget keeps the last reading meanwhile.
        Result.success()
    }
}

/** The phone's approximate location, with the coarse permission only; rounded to about 10 km before it is kept. */
object WeatherLocation {
    fun granted(context: Context): Boolean =
        ContextCompat.checkSelfPermission(context, Manifest.permission.ACCESS_COARSE_LOCATION) == PackageManager.PERMISSION_GRANTED

    /** The newest location any provider already has (no new fix), or null. */
    fun lastKnown(context: Context): WeatherPlace? {
        if (!granted(context)) return null
        val lm = context.getSystemService(LocationManager::class.java) ?: return null
        return try {
            lm.getProviders(true).mapNotNull { catching { lm.getLastKnownLocation(it) }.getOrNull() }
                .maxByOrNull { it.time }?.let { WeatherPlace.rounded(it.latitude, it.longitude, null) }
        } catch (_: SecurityException) {
            null
        }
    }

    /** A new approximate fix (Android 11 and later), else the last known one; null after 20 s. */
    suspend fun current(context: Context): WeatherPlace? {
        if (!granted(context)) return null
        val lm = context.getSystemService(LocationManager::class.java) ?: return lastKnown(context)
        if (Build.VERSION.SDK_INT < Build.VERSION_CODES.R) return lastKnown(context)
        val provider = listOf(LocationManager.FUSED_PROVIDER, LocationManager.NETWORK_PROVIDER, LocationManager.GPS_PROVIDER)
            .firstOrNull { catching { lm.isProviderEnabled(it) }.getOrDefault(false) } ?: return lastKnown(context)
        val loc: Location? = withTimeoutOrNull(20_000) {
            suspendCancellableCoroutine { c ->
                val cancel = android.os.CancellationSignal()
                c.invokeOnCancellation { cancel.cancel() }
                try {
                    lm.getCurrentLocation(provider, cancel, ContextCompat.getMainExecutor(context)) { c.resume(it) }
                } catch (_: SecurityException) {
                    c.resume(null)
                }
            }
        }
        return loc?.let { WeatherPlace.rounded(it.latitude, it.longitude, null) } ?: lastKnown(context)
    }
}
