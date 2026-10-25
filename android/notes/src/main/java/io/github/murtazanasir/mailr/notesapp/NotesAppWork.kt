package io.github.murtazanasir.mailr.notesapp

import io.github.murtazanasir.mailr.util.catching

import android.content.Context
import androidx.work.Constraints
import androidx.work.CoroutineWorker
import androidx.work.ExistingPeriodicWorkPolicy
import androidx.work.NetworkType
import androidx.work.PeriodicWorkRequestBuilder
import androidx.work.WorkManager
import androidx.work.WorkerParameters
import io.github.murtazanasir.mailr.notesapp.widget.Widgets
import java.util.concurrent.TimeUnit

/**
 * The periodic sync while the app is in the background (the widgets update
 * from it when no live stream runs): send what waits, read what changed, plan the note reminders again
 * and redraw the widgets.
 */
object NotesAppWork {
    private const val NAME = "notes-periodic"

    fun schedulePeriodic(context: Context) {
        WorkManager.getInstance(context).enqueueUniquePeriodicWork(NAME, ExistingPeriodicWorkPolicy.KEEP,
            PeriodicWorkRequestBuilder<NotesPeriodicWorker>(30, TimeUnit.MINUTES)
                .setConstraints(Constraints.Builder().setRequiredNetworkType(NetworkType.CONNECTED).build()).build())
    }

    fun cancel(context: Context) {
        WorkManager.getInstance(context).cancelUniqueWork(NAME)
    }
}

class NotesPeriodicWorker(ctx: Context, params: WorkerParameters) : CoroutineWorker(ctx, params) {
    override suspend fun doWork(): Result {
        val g = (applicationContext as? NotesApplication)?.graph ?: return Result.success()
        if (g.sessions.session.value == null) return Result.success()
        catching { g.notes.repo.drain() }
        catching { g.notes.repo.pull() }
        catching { g.reminders.sync() }
        catching { Widgets.updateAll(applicationContext) }
        return Result.success()
    }
}
