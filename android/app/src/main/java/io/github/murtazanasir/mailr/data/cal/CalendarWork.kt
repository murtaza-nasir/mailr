package io.github.murtazanasir.mailr.data.cal

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
import androidx.work.workDataOf
import io.github.murtazanasir.mailr.MailrApp
import io.github.murtazanasir.mailr.data.sync.BackgroundSchedule
import io.github.murtazanasir.mailr.data.sync.Work
import java.util.UUID
import java.util.concurrent.TimeUnit

object CalendarWork {
    private val online = Constraints.Builder().setRequiredNetworkType(NetworkType.CONNECTED).build()

    private const val PREFS = "mailr_background"
    private const val K_MINUTES = "calendar_minutes"

    /**
     * Calendar sync and the reminder watchdog, every [minutes] (BackgroundSchedule: 15 while push does
     * not work, 60 with working push). The watchdog needs no network: if an alarm was ever lost
     * (force-stop, OEM killers), it posts what is overdue and re-arms. Exact alarms cover the normal
     * path (ReminderEngine).
     */
    fun schedule(context: Context, minutes: Int = BackgroundSchedule.FAST_MINUTES) {
        val wm = WorkManager.getInstance(context)
        val m = minutes.coerceAtLeast(15).toLong()
        wm.enqueueUniquePeriodicWork("cal-sync", ExistingPeriodicWorkPolicy.UPDATE,
            PeriodicWorkRequestBuilder<CalendarSyncWorker>(m, TimeUnit.MINUTES).setConstraints(online).build())
        wm.enqueueUniquePeriodicWork("reminder-tick", ExistingPeriodicWorkPolicy.UPDATE,
            PeriodicWorkRequestBuilder<ReminderTickWorker>(m, TimeUnit.MINUTES).build())
        context.getSharedPreferences(PREFS, Context.MODE_PRIVATE).edit().putInt(K_MINUTES, m.toInt()).apply()
    }

    /** Schedules again only when the period should change (push started or stopped working). */
    fun follow(context: Context, minutes: Int) {
        val now = context.getSharedPreferences(PREFS, Context.MODE_PRIVATE).getInt(K_MINUTES, -1)
        if (now != minutes) schedule(context, minutes)
    }

    fun syncNow(context: Context) {
        WorkManager.getInstance(context).enqueueUniqueWork("cal-sync-now", ExistingWorkPolicy.REPLACE,
            OneTimeWorkRequestBuilder<CalendarSyncWorker>().setConstraints(online)
                .setBackoffCriteria(BackoffPolicy.EXPONENTIAL, 30, TimeUnit.SECONDS).build())
    }

    /** A deletion that waits out its Undo window, then joins the offline queue. */
    fun delayed(context: Context, op: DelayedOp, delayMs: Long) {
        WorkManager.getInstance(context).enqueueUniqueWork(op.name, ExistingWorkPolicy.REPLACE,
            OneTimeWorkRequestBuilder<DelayedOpWorker>()
                .setInitialDelay(delayMs, TimeUnit.MILLISECONDS)
                .setInputData(workDataOf("method" to op.method, "path" to op.path, "description" to op.description))
                .build())
    }

    fun cancel(context: Context, name: String) {
        WorkManager.getInstance(context).cancelUniqueWork(name)
    }
}

class CalendarSyncWorker(ctx: Context, params: WorkerParameters) : CoroutineWorker(ctx, params) {
    override suspend fun doWork(): Result {
        val g = (applicationContext as MailrApp).graph
        if (g.sessions.session.value == null) return Result.success()
        if (!g.calendar.sync()) return Result.retry()
        g.pullReminders()
        // Push that went quiet: back to every 15 minutes (and back to hourly once it works again).
        g.followBackgroundCadence()
        return Result.success()
    }
}

class ReminderTickWorker(ctx: Context, params: WorkerParameters) : CoroutineWorker(ctx, params) {
    override suspend fun doWork(): Result {
        val g = (applicationContext as MailrApp).graph
        if (g.sessions.session.value == null) return Result.success()
        g.reminders.fire()
        g.followBackgroundCadence()
        return Result.success()
    }
}

class DelayedOpWorker(ctx: Context, params: WorkerParameters) : CoroutineWorker(ctx, params) {
    override suspend fun doWork(): Result {
        val g = (applicationContext as MailrApp).graph
        val method = inputData.getString("method") ?: return Result.failure()
        val path = inputData.getString("path") ?: return Result.failure()
        g.mail.enqueue(method, path, "{}", UUID.randomUUID().toString(), inputData.getString("description") ?: path)
        Work.drainOutbox(applicationContext)
        return Result.success()
    }
}
