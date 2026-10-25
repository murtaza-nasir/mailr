package io.github.murtazanasir.mailr.data.share

import io.github.murtazanasir.mailr.util.catching

import android.Manifest
import android.app.PendingIntent
import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent
import android.content.pm.PackageManager
import androidx.core.app.NotificationCompat
import androidx.core.app.NotificationManagerCompat
import androidx.core.app.Person
import androidx.core.content.ContextCompat
import androidx.core.content.pm.ShortcutInfoCompat
import androidx.core.content.pm.ShortcutManagerCompat
import androidx.core.graphics.drawable.IconCompat
import androidx.work.BackoffPolicy
import androidx.work.Constraints
import androidx.work.CoroutineWorker
import androidx.work.ExistingWorkPolicy
import androidx.work.ForegroundInfo
import androidx.work.NetworkType
import androidx.work.OneTimeWorkRequestBuilder
import androidx.work.OutOfQuotaPolicy
import androidx.work.WorkManager
import androidx.work.WorkerParameters
import androidx.work.workDataOf
import io.github.murtazanasir.mailr.MailrApp
import io.github.murtazanasir.mailr.R
import io.github.murtazanasir.mailr.data.db.SelfSendEntity
import io.github.murtazanasir.mailr.data.sync.Channels
import io.github.murtazanasir.mailr.ui.MainActivity
import io.github.murtazanasir.mailr.ui.share.ShareToSelfActivity
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.launch
import java.util.concurrent.TimeUnit

object SelfSendWork {
    fun enqueue(context: Context, id: String) {
        WorkManager.getInstance(context).enqueueUniqueWork(
            "self-send-$id", ExistingWorkPolicy.REPLACE,
            OneTimeWorkRequestBuilder<SelfSendWorker>()
                .setInputData(workDataOf("id" to id))
                .setConstraints(Constraints.Builder().setRequiredNetworkType(NetworkType.CONNECTED).build())
                .setExpedited(OutOfQuotaPolicy.RUN_AS_NON_EXPEDITED_WORK_REQUEST)
                .setBackoffCriteria(BackoffPolicy.EXPONENTIAL, 10, TimeUnit.SECONDS)
                .addTag("self-send")
                .build(),
        )
    }
}

/** Sends one queued "Send to self" share; retried with backoff while offline or on 5xx. */
class SelfSendWorker(ctx: Context, params: WorkerParameters) : CoroutineWorker(ctx, params) {
    override suspend fun doWork(): Result {
        val id = inputData.getString("id") ?: return Result.failure()
        val g = (applicationContext as MailrApp).graph
        if (g.sessions.session.value == null) {
            g.db.selfSends().setState(id, SelfState.FAILED, applicationContext.getString(R.string.sharing_sign_in_to_send))
            g.db.selfSends().get(id)?.let { SelfSendNotifier.failed(applicationContext, it) }
            return Result.success()
        }
        return when (g.selfSend.process(id)) {
            SelfSendRepository.Outcome.DONE -> Result.success()
            SelfSendRepository.Outcome.RETRY -> Result.retry()
        }
    }

    /** Only used for expedited work on Android 11 and older. */
    override suspend fun getForegroundInfo(): ForegroundInfo = ForegroundInfo(
        SelfSendNotifier.PROGRESS_ID,
        NotificationCompat.Builder(applicationContext, Channels.SENDING)
            .setSmallIcon(R.drawable.ic_launcher_monochrome)
            .setContentTitle(applicationContext.getString(R.string.self_sending))
            .setProgress(0, 0, true).setOngoing(true).setSilent(true).build(),
    )
}

object SelfSendNotifier {
    const val PROGRESS_ID = 7101
    private const val ACTION_RETRY = "io.github.murtazanasir.mailr.SELF_SEND_RETRY"

    private fun tag(id: String) = "self-send-$id"

    fun failed(context: Context, e: SelfSendEntity) {
        if (ContextCompat.checkSelfPermission(context, Manifest.permission.POST_NOTIFICATIONS) != PackageManager.PERMISSION_GRANTED) return
        val retry = PendingIntent.getBroadcast(context, e.id.hashCode(),
            Intent(context, SelfSendReceiver::class.java).setAction(ACTION_RETRY).putExtra("id", e.id),
            PendingIntent.FLAG_IMMUTABLE or PendingIntent.FLAG_UPDATE_CURRENT)
        val open = PendingIntent.getActivity(context, 0,
            Intent(context, MainActivity::class.java).addFlags(Intent.FLAG_ACTIVITY_NEW_TASK),
            PendingIntent.FLAG_IMMUTABLE)
        val what = e.subject.ifBlank { e.text.lineSequence().firstOrNull().orEmpty() }.ifBlank { context.getString(R.string.sharing_your_share) }
        NotificationManagerCompat.from(context).notify(tag(e.id), 1, NotificationCompat.Builder(context, Channels.SENDING)
            .setSmallIcon(R.drawable.ic_launcher_monochrome)
            .setContentTitle(context.getString(R.string.self_failed))
            .setContentText(what)
            .setStyle(NotificationCompat.BigTextStyle().bigText("$what\n${e.error.orEmpty()}".trim()))
            .setCategory(NotificationCompat.CATEGORY_ERROR)
            .setAutoCancel(true)
            .setContentIntent(open)
            .addAction(0, context.getString(R.string.action_try_again), retry)
            .build())
    }

    fun clear(context: Context, id: String) = NotificationManagerCompat.from(context).cancel(tag(id), 1)

    fun isRetry(intent: Intent) = intent.action == ACTION_RETRY
}

class SelfSendReceiver : BroadcastReceiver() {
    override fun onReceive(context: Context, intent: Intent) {
        if (!SelfSendNotifier.isRetry(intent)) return
        val id = intent.getStringExtra("id") ?: return
        val pending = goAsync()
        CoroutineScope(Dispatchers.IO).launch {
            try {
                SelfSendNotifier.clear(context, id)
                (context.applicationContext as MailrApp).graph.selfSend.retry(id)
            } finally {
                pending.finish()
            }
        }
    }
}

/**
 * The "Send to self" sharing shortcut. Direct Share ranks shortcuts above plain share targets,
 * so after a few uses it sits in the first row of the share sheet. Long-pressing the launcher
 * icon shows it too, and opens a quick note to self.
 */
object SelfShareShortcut {
    const val ID = "send-to-self"
    const val CATEGORY = "io.github.murtazanasir.mailr.category.SEND_TO_SELF"

    fun publish(context: Context, email: String) {
        val sc = ShortcutInfoCompat.Builder(context, ID)
            .setShortLabel(context.getString(R.string.share_self_label))
            .setLongLabel(context.getString(R.string.share_self_long))
            .setIcon(IconCompat.createWithResource(context, R.mipmap.ic_send_to_self))
            .setIntent(Intent(context, ShareToSelfActivity::class.java).setAction(Intent.ACTION_VIEW))
            .setLongLived(true)
            .setCategories(setOf(CATEGORY))
            .setPerson(Person.Builder().setName(context.getString(R.string.share_self_person)).setKey("mailto:$email").build())
            .build()
        catching { ShortcutManagerCompat.pushDynamicShortcut(context, sc) }
    }

    fun used(context: Context) {
        catching { ShortcutManagerCompat.reportShortcutUsed(context, ID) }
    }

    fun remove(context: Context) {
        catching {
            ShortcutManagerCompat.removeLongLivedShortcuts(context, listOf(ID))
            ShortcutManagerCompat.removeDynamicShortcuts(context, listOf(ID))
        }
    }
}
