package io.github.murtazanasir.mailr.data.recorder

import io.github.murtazanasir.mailr.util.catching

import android.app.Notification
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.app.Service
import android.content.Context
import android.content.Intent
import android.content.pm.ServiceInfo
import android.net.Uri
import android.os.Build
import android.os.IBinder
import androidx.core.app.NotificationCompat
import androidx.core.app.ServiceCompat
import androidx.work.BackoffPolicy
import androidx.work.Constraints
import androidx.work.CoroutineWorker
import androidx.work.ExistingWorkPolicy
import androidx.work.NetworkType
import androidx.work.OneTimeWorkRequestBuilder
import androidx.work.WorkManager
import androidx.work.WorkerParameters
import androidx.work.workDataOf
import io.github.murtazanasir.mailr.MailrApp
import io.github.murtazanasir.mailr.R
import io.github.murtazanasir.mailr.ui.MainActivity
import kotlinx.coroutines.launch
import java.util.concurrent.TimeUnit

/** The notification channel of a recording in progress (quiet: it is there while recording). */
object RecorderChannel {
    const val ID = "recording"
    const val NOTIFICATION_ID = 7301

    fun create(context: Context) {
        context.getSystemService(NotificationManager::class.java).createNotificationChannel(
            NotificationChannel(ID, context.getString(R.string.channel_recording), NotificationManager.IMPORTANCE_LOW)
                .apply { description = context.getString(R.string.channel_recording_desc); setShowBadge(false) })
    }
}

/**
 * Keeps the app running while it records (a foreground service of type microphone), with a
 * notification that shows the time and offers Pause or Resume and Stop. The recording itself is
 * [RecorderController]'s; this service only follows it and stops when it ends.
 */
class RecorderService : Service() {
    override fun onBind(intent: Intent?): IBinder? = null

    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int {
        val g = (application as MailrApp).graph
        when (intent?.action) {
            ACTION_PAUSE -> g.scope.launch { g.recorder.pause() }
            ACTION_RESUME -> g.scope.launch { g.recorder.resume() }
            ACTION_STOP -> g.scope.launch { g.recorder.stop() }
        }
        val s = g.recorder.current.value
        if (s == null || !s.live) {
            ServiceCompat.stopForeground(this, ServiceCompat.STOP_FOREGROUND_REMOVE)
            stopSelf()
            return START_NOT_STICKY
        }
        ServiceCompat.startForeground(this, RecorderChannel.NOTIFICATION_ID, notification(this, s),
            if (Build.VERSION.SDK_INT >= 30) ServiceInfo.FOREGROUND_SERVICE_TYPE_MICROPHONE else 0)
        // Not sticky: after a kill the controller recovers the file and uploads it; it does not
        // start recording again on its own.
        return START_NOT_STICKY
    }

    companion object {
        const val ACTION_PAUSE = "io.github.murtazanasir.mailr.recorder.PAUSE"
        const val ACTION_RESUME = "io.github.murtazanasir.mailr.recorder.RESUME"
        const val ACTION_STOP = "io.github.murtazanasir.mailr.recorder.STOP"
        const val ACTION_UPDATE = "io.github.murtazanasir.mailr.recorder.UPDATE"

        private fun action(context: Context, action: String) = PendingIntent.getService(context, action.hashCode(),
            Intent(context, RecorderService::class.java).setAction(action), PendingIntent.FLAG_IMMUTABLE or PendingIntent.FLAG_UPDATE_CURRENT)

        fun notification(context: Context, s: RecordingSession, now: Long = System.currentTimeMillis()): Notification {  // clock-ok: notification chronometer
            val open = PendingIntent.getActivity(context, 7301,
                io.github.murtazanasir.mailr.ui.util.InternalLinks.mark(context, Intent(Intent.ACTION_VIEW, Uri.parse("mailr://record"), context, MainActivity::class.java)),
                PendingIntent.FLAG_IMMUTABLE or PendingIntent.FLAG_UPDATE_CURRENT)
            val recording = s.state == RecState.RECORDING
            val b = NotificationCompat.Builder(context, RecorderChannel.ID)
                .setSmallIcon(android.R.drawable.ic_btn_speak_now)
                .setContentTitle(context.getString(if (recording) R.string.rec_notif_recording else R.string.rec_notif_paused))
                .setContentText(s.meta.title.ifBlank { context.getString(R.string.rec_notif_new) })
                .setOngoing(true)
                .setOnlyAlertOnce(true)
                .setCategory(NotificationCompat.CATEGORY_SERVICE)
                .setForegroundServiceBehavior(NotificationCompat.FOREGROUND_SERVICE_IMMEDIATE)
                .setContentIntent(open)
                .setWhen(now - s.elapsedAt(now))
                .setUsesChronometer(recording)
                .setShowWhen(recording)
            if (recording) b.addAction(0, context.getString(R.string.rec_notif_pause), action(context, ACTION_PAUSE))
            else b.addAction(0, context.getString(R.string.rec_notif_resume), action(context, ACTION_RESUME))
            b.addAction(0, context.getString(R.string.rec_notif_stop), action(context, ACTION_STOP))
            return b.build()
        }
    }
}

/** The hooks a running app gives the controller: the service and WorkManager. */
class AndroidRecorderHooks(private val context: Context) : RecorderHooks {
    override fun recording(session: RecordingSession) {
        val i = Intent(context, RecorderService::class.java).setAction(RecorderService.ACTION_UPDATE)
        catching { androidx.core.content.ContextCompat.startForegroundService(context, i) }
    }

    override fun idle() {
        catching { context.startService(Intent(context, RecorderService::class.java).setAction(RecorderService.ACTION_UPDATE)) }
    }

    override fun enqueueUpload(id: String) = RecordingUploadWork.enqueue(context, id)
}

/**
 * Uploads one recording through mailr when there is a network, with back-off; network errors and
 * server errors are retried, a refusal is not (RecorderController.uploadNow).
 */
class RecordingUploadWork(context: Context, params: WorkerParameters) : CoroutineWorker(context, params) {
    override suspend fun doWork(): Result {
        val id = inputData.getString(KEY) ?: return Result.failure()
        val g = (applicationContext as MailrApp).graph
        if (g.sessions.session.value == null) return Result.retry()
        return when (g.recorder.uploadNow(id)) {
            UploadOutcome.DONE -> Result.success()
            UploadOutcome.RETRY -> Result.retry()
            UploadOutcome.FINAL -> Result.failure()
        }
    }

    companion object {
        private const val KEY = "id"

        fun enqueue(context: Context, id: String) {
            WorkManager.getInstance(context).enqueueUniqueWork("recording-upload-$id", ExistingWorkPolicy.KEEP,
                OneTimeWorkRequestBuilder<RecordingUploadWork>()
                    .setInputData(workDataOf(KEY to id))
                    .setConstraints(Constraints.Builder().setRequiredNetworkType(NetworkType.CONNECTED).build())
                    .setBackoffCriteria(BackoffPolicy.EXPONENTIAL, 30, TimeUnit.SECONDS)
                    .build())
        }
    }
}
