package io.github.murtazanasir.mailr.data.phonecal

import android.content.Context
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

/** The busy-times sync of phone calendars: every 30 minutes, and soon after a change (network required). */
object PhoneCalendarWork {
    private val online = Constraints.Builder().setRequiredNetworkType(NetworkType.CONNECTED).build()
    const val PERIODIC = "phonecal-sync"
    const val SOON = "phonecal-soon"

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
        if (g.sessions.session.value == null) return Result.success()
        return when (g.phoneCalendars.sync()) {
            PhoneCalendars.SyncResult.DONE -> Result.success()
            // Offline or the server busy: the next run sends the snapshot of that moment.
            PhoneCalendars.SyncResult.RETRY -> Result.retry()
        }
    }
}
