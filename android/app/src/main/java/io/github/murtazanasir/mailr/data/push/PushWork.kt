package io.github.murtazanasir.mailr.data.push

import io.github.murtazanasir.mailr.util.catching

import android.content.Context
import androidx.core.app.NotificationCompat
import androidx.work.BackoffPolicy
import androidx.work.Constraints
import androidx.work.CoroutineWorker
import androidx.work.Data
import androidx.work.ExistingWorkPolicy
import androidx.work.ForegroundInfo
import androidx.work.NetworkType
import androidx.work.OneTimeWorkRequestBuilder
import androidx.work.OutOfQuotaPolicy
import androidx.work.WorkManager
import androidx.work.WorkerParameters
import io.github.murtazanasir.mailr.AppGraph
import io.github.murtazanasir.mailr.MailrApp
import io.github.murtazanasir.mailr.R
import io.github.murtazanasir.mailr.data.api.Approval
import io.github.murtazanasir.mailr.data.api.ApprovalPreview
import io.github.murtazanasir.mailr.data.settings.NotifyMode
import io.github.murtazanasir.mailr.data.sync.Channels
import io.github.murtazanasir.mailr.data.sync.Notifier
import io.github.murtazanasir.mailr.data.sync.notifiedApprovals
import io.github.murtazanasir.mailr.data.sync.notifiedConversations
import io.github.murtazanasir.mailr.data.sync.notifyAssistant
import io.github.murtazanasir.mailr.data.sync.notifyLock
import io.github.murtazanasir.mailr.data.sync.pollApprovals
import io.github.murtazanasir.mailr.data.sync.pollNewMail
import kotlinx.coroutines.flow.first
import kotlinx.coroutines.launch
import kotlinx.coroutines.sync.withLock
import org.unifiedpush.android.connector.FailedReason
import org.unifiedpush.android.connector.PushService
import org.unifiedpush.android.connector.data.PushEndpoint
import org.unifiedpush.android.connector.data.PushMessage
import java.util.concurrent.TimeUnit

/**
 * The UnifiedPush connector's service (manifest: org.unifiedpush.android.connector.PUSH_EVENT).
 * The connector binds it for a few seconds per event, so slow work goes to WorkManager.
 */
class MailrPushService : PushService() {
    private val g: AppGraph get() = (application as MailrApp).graph

    override fun onNewEndpoint(endpoint: PushEndpoint, instance: String) {
        g.push.onNewEndpoint(endpoint.url, endpoint.pubKeySet?.pubKey, endpoint.pubKeySet?.auth)
    }

    override fun onMessage(message: PushMessage, instance: String) {
        if (!PushWork.accept(message.decrypted, g.push.info.value.pubKey != null)) return
        g.push.onMessage()
        PushWork.handle(this, message.content, trusted = message.decrypted)
    }

    override fun onRegistrationFailed(reason: FailedReason, instance: String) = g.push.onRegistrationFailed(reason)

    override fun onUnregistered(instance: String) {
        g.scope.launch { g.push.onUnregistered() }
    }

    override fun onTempUnavailable(instance: String) = g.push.onTempUnavailable()
}

object PushWork {
    private val online = Constraints.Builder().setRequiredNetworkType(NetworkType.CONNECTED).build()

    /** Send this phone's endpoint to mailr once there is a network (PushRegistrar.upload). */
    fun upload(context: Context) {
        WorkManager.getInstance(context).enqueueUniqueWork("push-upload", ExistingWorkPolicy.REPLACE,
            OneTimeWorkRequestBuilder<PushUploadWorker>().setConstraints(online)
                .setBackoffCriteria(BackoffPolicy.EXPONENTIAL, 30, TimeUnit.SECONDS).build())
    }

    /**
     * Handle one message: decode it now, act in an expedited job. [trusted]: the connector
     * decrypted it with this phone's keys (or the phone gave none), so its text may be shown.
     */
    fun handle(context: Context, content: ByteArray, trusted: Boolean) {
        // One named queue: a burst of messages runs one after another, not as that many parallel
        // jobs (each read the server and spent expedited quota). Every message still runs: each can
        // carry text of its own.
        WorkManager.getInstance(context).enqueueUniqueWork("push-message", ExistingWorkPolicy.APPEND_OR_REPLACE,
            OneTimeWorkRequestBuilder<PushMessageWorker>()
                .setInputData(Data.Builder().putByteArray(KEY_BODY, content.copyOf(minOf(content.size, 8000)))
                    .putBoolean(KEY_TRUSTED, trusted).build())
                .setExpedited(OutOfQuotaPolicy.RUN_AS_NON_EXPEDITED_WORK_REQUEST)
                .build())
    }

    const val KEY_BODY = "body"
    const val KEY_TRUSTED = "trusted"

    @Volatile private var lastUntrusted = 0L

    /**
     * Whether a message is acted on at all. Once this phone has given the server its keys, a
     * message that was not encrypted to them did not come from mailr and is dropped. Without
     * keys (an old registration) a plain message only starts a sync, at most once a minute,
     * so posting to the push address cannot drain the battery.
     */
    fun accept(decrypted: Boolean, hasKeys: Boolean, now: Long = System.currentTimeMillis()): Boolean {  // clock-ok: rate limit
        if (decrypted) return true
        if (hasKeys) return false
        synchronized(this) {
            if (now - lastUntrusted < 60_000) return false
            lastUntrusted = now
        }
        return true
    }

    /** Tests: forget the last plain message. */
    internal fun resetRateLimit() { lastUntrusted = 0L }
}

class PushUploadWorker(ctx: Context, params: WorkerParameters) : CoroutineWorker(ctx, params) {
    override suspend fun doWork(): Result =
        if ((applicationContext as MailrApp).graph.push.upload()) Result.success() else Result.retry()
}

class PushMessageWorker(ctx: Context, params: WorkerParameters) : CoroutineWorker(ctx, params) {
    override suspend fun doWork(): Result {
        val g = (applicationContext as MailrApp).graph
        if (g.sessions.session.value == null) return Result.success()
        val event = PushPayload.decode(inputData.getByteArray(PushWork.KEY_BODY) ?: return Result.success())
            ?: return Result.success()
        PushHandler.handle(g, event, inputData.getBoolean(PushWork.KEY_TRUSTED, false))
        return Result.success()
    }

    /** Only used for expedited work on Android 11 and older. */
    override suspend fun getForegroundInfo(): ForegroundInfo = ForegroundInfo(7201,
        NotificationCompat.Builder(applicationContext, Channels.MAIL)
            .setSmallIcon(R.drawable.ic_launcher_monochrome)
            .setContentTitle(applicationContext.getString(R.string.push_checking_mail))
            .setOngoing(true).setSilent(true).build())
}

/**
 * What a push message does. The message says what changed; the app reads the change from the
 * server and notifies through the same code as the periodic poll, so the two never notify the
 * same conversation or approval twice (they share the "already notified" lists and
 * [notifyLock]). Only when the server cannot be read does the message's own text show, and
 * only for a [trusted] (decrypted) message. In the foreground nothing is posted: the open app
 * shows changes live.
 */
object PushHandler {
    suspend fun handle(g: AppGraph, e: PushEvent, trusted: Boolean) {
        val prefs = g.settings.prefs.first()
        val background = !g.foreground.value
        when (e) {
            is PushEvent.NewMail -> {
                g.mail.invalidate()
                if (background) newMail(g, prefs.mailNotify(), e, trusted)
                // The inbox the app opens on, fresh in the cache.
                catching { g.mail.section(io.github.murtazanasir.mailr.data.repo.MailView()) }
            }
            is PushEvent.Approval -> {
                catching { io.github.murtazanasir.mailr.ui.approvals.ApprovalsTracker.of(g).refresh() }
                if (background && prefs.notifyApprovals) approval(g, e, trusted)
            }
            // ACT approval.sync @android.notification: answered elsewhere, or expired. A decrypted message's
            // ids go at once; the read of the list then takes down any other that is no longer waiting.
            is PushEvent.ApprovalDecided -> {
                if (trusted) io.github.murtazanasir.mailr.data.sync.approvalsDecided(g, e.keys)
                catching { io.github.murtazanasir.mailr.ui.approvals.ApprovalsTracker.of(g).refresh() }
            }
            is PushEvent.SendFailed -> {
                g.mail.invalidate()
                if (background) sendFailed(g, e, trusted)
            }
            is PushEvent.Assistant -> if (background) notifyAssistant(g)
            // A mail account needs its password again (the server's core/accounthealth.py): once per failure.
            is PushEvent.Account -> {
                g.mail.invalidate(setOf("connector"))
                Notifier.accountSignin(g.context, g.sessions.server, if (trusted) e.title else "", if (trusted) e.body else "")
            }
            is PushEvent.Test -> Notifier.pushTest(g.context)
            is PushEvent.Other -> {
                g.mail.invalidate()
                // A change from another device may have read or archived a notified conversation.
                if (background) catching { io.github.murtazanasir.mailr.data.sync.MailNotifications.reconcile(g) }
            }
        }
    }

    private suspend fun newMail(g: AppGraph, mode: NotifyMode, e: PushEvent.NewMail, trusted: Boolean) {
        if (pollNewMail(g, mode)) return
        // The server could not be read: say who wrote, if the user's setting covers it.
        val id = e.conversationId ?: return
        val wanted = mode == NotifyMode.ALL || (mode == NotifyMode.IMPORTANT && e.important)
        if (!trusted || !wanted) return
        notifyLock.withLock { if (id !in notifiedConversations(g)) Notifier.newMailHint(g.context, id, e.title) }
    }

    private suspend fun approval(g: AppGraph, e: PushEvent.Approval, trusted: Boolean) {
        if (pollApprovals(g)) return
        val key = e.key ?: return
        if (!trusted) return
        notifyLock.withLock {
            if (key in notifiedApprovals(g)) return@withLock
            val id = e.outboxId ?: e.pendingId ?: return@withLock
            Notifier.approval(g.context, Approval(id = id, outboxId = e.outboxId, actor = e.actor ?: actorFromTitle(e.title),
                action = e.action.orEmpty(), preview = ApprovalPreview(e.title.ifBlank { g.context.getString(R.string.push_approval_waiting) },
                    listOf(e.body).filter { it.isNotBlank() })))
        }
    }

    /** The agent's name from an approval title when an older server sends no `actor`: the
     *  current "Approval request from <agent>", or the earlier "<agent> needs your approval". */
    internal fun actorFromTitle(title: String): String =
        if (title.startsWith("Approval request from ")) title.removePrefix("Approval request from ")
        else title.substringBefore(" needs your approval", "")

    private const val KV_SEND_FAILED = "send_failed_notified"

    private suspend fun sendFailed(g: AppGraph, e: PushEvent.SendFailed, trusted: Boolean) {
        val id = e.outboxId ?: return
        notifyLock.withLock {
            val seen = g.mail.kv(KV_SEND_FAILED)?.split(',')?.mapNotNull { it.toLongOrNull() }.orEmpty()
            if (id in seen) return@withLock
            // The failed message as the server has it; the push text only when that cannot be read.
            val ob = catching { g.api.outbox("failed,uncertain") }.getOrNull()?.firstOrNull { it.id == id }
            val text = when {
                ob != null -> g.context.getString(R.string.push_send_failed_to, ob.recipients.take(2).joinToString(", "), ob.error).trim()
                trusted -> e.body
                else -> g.context.getString(R.string.push_send_failed_generic)
            }
            val title = when {
                trusted -> e.title
                ob?.status == "uncertain" -> g.context.getString(R.string.push_send_uncertain_title)
                else -> g.context.getString(R.string.push_send_failed_title)
            }
            Notifier.sendFailed(g.context, id, title, text)
            g.mail.putKv(KV_SEND_FAILED, (listOf(id) + seen).take(50).joinToString(","))
        }
    }
}
