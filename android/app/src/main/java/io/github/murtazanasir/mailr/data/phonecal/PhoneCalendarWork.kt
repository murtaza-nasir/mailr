package io.github.murtazanasir.mailr.data.phonecal

import android.content.Context
import android.provider.CalendarContract
import androidx.work.BackoffPolicy
import androidx.work.Constraints
import androidx.work.CoroutineWorker
import androidx.work.ExistingPeriodicWorkPolicy
import androidx.work.ExistingWorkPolicy
import androidx.work.NetworkType
import androidx.work.OneTimeWorkRequestBuilder
import androidx.work.PeriodicWorkRequestBuilder
import androidx.work.WorkManager
import androidx.work.WorkerParameters
import io.github.murtazanasir.mailr.MailrApp
import java.util.concurrent.TimeUnit

/**
 * The busy-times sync of phone calendars: every 30 minutes, soon after a change while mailr runs
 * (the ContentObserver in PhoneCalendars), and on a change to the calendar provider while mailr is
 * not running ([watch]: a content-URI trigger, which Android delivers to a stopped app too).
 */
object PhoneCalendarWork {
    private val online = Constraints.Builder().setRequiredNetworkType(NetworkType.CONNECTED).build()
    const val PERIODIC = "phonecal-sync"
    const val SOON = "phonecal-soon"
    const val PROVIDER = "phonecal-provider"

    /** A run when the phone's calendars change (1 minute after the last change, at most 5 minutes
     *  after the first). A content trigger fires once, so each run sets the next one ([rearm]). */
    fun watch(context: Context, on: Boolean) {
        val wm = WorkManager.getInstance(context)
        if (on) wm.enqueueUniqueWork(PROVIDER, ExistingWorkPolicy.KEEP, providerRequest())
        else wm.cancelUniqueWork(PROVIDER)
    }

    /** From inside a provider-triggered run: the next trigger, queued after this run ends. */
    fun rearm(context: Context) {
        WorkManager.getInstance(context).enqueueUniqueWork(PROVIDER, ExistingWorkPolicy.APPEND_OR_REPLACE, providerRequest())
    }

    private fun providerRequest() = OneTimeWorkRequestBuilder<PhoneCalendarWorker>()
        .setConstraints(Constraints.Builder().setRequiredNetworkType(NetworkType.CONNECTED)
            .addContentUriTrigger(CalendarContract.Events.CONTENT_URI, true)
            .setTriggerContentUpdateDelay(1, TimeUnit.MINUTES)
            .setTriggerContentMaxDelay(5, TimeUnit.MINUTES).build())
        .addTag(PROVIDER)
        .build()

    fun periodic(context: Context, on: Boolean) {
        val wm = WorkManager.getInstance(context)
        if (on) wm.enqueueUniquePeriodicWork(PERIODIC, ExistingPeriodicWorkPolicy.KEEP,
            PeriodicWorkRequestBuilder<PhoneCalendarWorker>(30, TimeUnit.MINUTES).setConstraints(online).build())
        else wm.cancelUniqueWork(PERIODIC)
    }

    /** A run after [delayMs]; a later call replaces a run still waiting, so a burst of changes sends once. */
    fun soon(context: Context, delayMs: Long) {
        WorkManager.getInstance(context).enqueueUniqueWork(SOON, ExistingWorkPolicy.REPLACE,
            OneTimeWorkRequestBuilder<PhoneCalendarWorker>().setConstraints(online)
                .setInitialDelay(delayMs, TimeUnit.MILLISECONDS)
                .setBackoffCriteria(BackoffPolicy.EXPONENTIAL, 30, TimeUnit.SECONDS).build())
    }
}

class PhoneCalendarWorker(ctx: Context, params: WorkerParameters) : CoroutineWorker(ctx, params) {
    override suspend fun doWork(): Result {
        val g = (applicationContext as MailrApp).graph
        // A provider-triggered run sets the next trigger first, so a change during this run is not missed.
        if (PhoneCalendarWork.PROVIDER in tags && g.phoneCalendars.sending()) PhoneCalendarWork.rearm(applicationContext)
        if (g.sessions.session.value == null) return Result.success()
        return when (g.phoneCalendars.sync()) {
            PhoneCalendars.SyncResult.DONE -> Result.success()
            // Offline or the server busy: the next run sends the snapshot of that moment.
            PhoneCalendars.SyncResult.RETRY -> Result.retry()
        }
    }
}
