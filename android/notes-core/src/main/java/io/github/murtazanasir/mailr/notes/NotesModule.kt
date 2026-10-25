package io.github.murtazanasir.mailr.notes

import io.github.murtazanasir.mailr.util.catching

import android.content.Context
import androidx.work.Constraints
import androidx.work.CoroutineWorker
import androidx.work.ExistingWorkPolicy
import androidx.work.NetworkType
import androidx.work.OneTimeWorkRequestBuilder
import androidx.work.WorkManager
import androidx.work.WorkerParameters
import io.github.murtazanasir.mailr.notes.data.NotesApi
import io.github.murtazanasir.mailr.notes.data.NotesDatabase
import io.github.murtazanasir.mailr.notes.data.NotesHttp
import io.github.murtazanasir.mailr.notes.data.NotesRepository
import io.github.murtazanasir.mailr.time.Timeline
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.launch
import java.util.concurrent.TimeUnit

/**
 * The Notes module as a host builds it (the mailr app in AppGraph; the Notes app in its
 * own graph): the API on the host's transport, the repository on the host's database, and the
 * drain of the replay queue, at once and again by WorkManager when the network comes back.
 */
class NotesModule(
    http: NotesHttp,
    database: NotesDatabase,
    private val scope: CoroutineScope,
    /** Schedules a background drain (WorkManager); null in tests. */
    private val scheduleDrain: (() -> Unit)? = null,
    /** Send a queued write at once (off in tests, which drain by hand). */
    private val drainAtOnce: Boolean = true,
    /** The repository's "now" and its user-visible timers (Timeline.Real; tests pass a virtual one). */
    time: Timeline = Timeline.Real,
) {
    val api = NotesApi(http)
    val repo = NotesRepository(api, database, scope, time)

    init {
        repo.onQueued = {
            scheduleDrain?.invoke()
            if (drainAtOnce) scope.launch { catching { repo.drain() } }
        }
    }

    /** The app came to the foreground or a push arrived: send what waits, then read what changed. */
    fun syncNow() = scope.launch {
        catching { repo.drain() }
        catching { repo.pull() }
    }
}

/** An Application that runs the Notes module ([NotesSyncWorker] finds it here). Null while signed out. */
interface NotesHost {
    val notesModule: NotesModule?
}

/** The replay queue's background drain: once there is a network, retried with backoff. */
class NotesSyncWorker(ctx: Context, params: WorkerParameters) : CoroutineWorker(ctx, params) {
    override suspend fun doWork(): Result {
        val m = (applicationContext as? NotesHost)?.notesModule ?: return Result.success()
        return when (m.repo.drain()) {
            NotesRepository.DrainResult.RETRY -> Result.retry()
            NotesRepository.DrainResult.PAUSED -> Result.success()
            NotesRepository.DrainResult.DONE -> {
                m.repo.pull()
                Result.success()
            }
        }
    }
}

object NotesWork {
    fun drainSoon(context: Context) {
        WorkManager.getInstance(context).enqueueUniqueWork(
            "notes-queue", ExistingWorkPolicy.KEEP,
            OneTimeWorkRequestBuilder<NotesSyncWorker>()
                .setConstraints(Constraints.Builder().setRequiredNetworkType(NetworkType.CONNECTED).build())
                .setBackoffCriteria(androidx.work.BackoffPolicy.EXPONENTIAL, 15, TimeUnit.SECONDS).build(),
        )
    }
}
