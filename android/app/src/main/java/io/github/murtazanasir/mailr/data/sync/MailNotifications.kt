package io.github.murtazanasir.mailr.data.sync

import io.github.murtazanasir.mailr.util.catching

import android.app.NotificationManager
import android.app.PendingIntent
import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent
import androidx.core.app.NotificationCompat
import androidx.core.app.NotificationManagerCompat
import androidx.core.app.RemoteInput
import io.github.murtazanasir.mailr.AppGraph
import io.github.murtazanasir.mailr.MailrApp
import io.github.murtazanasir.mailr.R
import io.github.murtazanasir.mailr.data.api.ComposePayload
import io.github.murtazanasir.mailr.data.api.ConversationSummary
import io.github.murtazanasir.mailr.data.api.ApiException
import io.github.murtazanasir.mailr.data.api.userMessage
import io.github.murtazanasir.mailr.data.reminders.async
import io.github.murtazanasir.mailr.ui.compose.ComposeBody
import androidx.work.BackoffPolicy
import androidx.work.Constraints
import androidx.work.CoroutineWorker
import androidx.work.ExistingWorkPolicy
import androidx.work.NetworkType
import androidx.work.OneTimeWorkRequestBuilder
import androidx.work.OutOfQuotaPolicy
import androidx.work.WorkManager
import androidx.work.WorkerParameters
import androidx.work.workDataOf
import java.io.IOException
import java.util.UUID
import java.util.concurrent.TimeUnit
import io.github.murtazanasir.mailr.i18n.AppText

/**
 * New-mail notifications beyond posting them: their Archive, Mark read and Reply buttons,
 * and taking them down once the conversation is dealt with anywhere.
 *
 * - Acted on in this app or from a button: [io.github.murtazanasir.mailr.data.repo.MailRepository.act]
 *   cancels the notification (Archive and Mark read go through it, so they queue offline).
 * - Read or archived on another device: [reconcile] runs after each poll, push and live change
 *   notice, and takes down every notification whose conversation is no longer unread in the inbox.
 */
object MailNotifications {
    /** Tag and id of a conversation's new-mail notification (Notifier.newMail, Notifier.newMailHint). */
    fun tag(id: Long) = "c$id"
    const val ID = 1
    const val SUMMARY_TAG = "mail-summary"

    fun cancel(context: Context, ids: List<Long>) {
        val nm = NotificationManagerCompat.from(context)
        ids.forEach { catching { nm.cancel(tag(it), ID) } }
        // The summary follows: its count and lines, or gone under two.
        Notifier.mailSummary(context)
    }

    /** The conversations with a new-mail notification showing now. */
    fun shown(context: Context): Set<Long> {
        val nm = context.getSystemService(NotificationManager::class.java) ?: return emptySet()
        return catching { nm.activeNotifications.toList() }.getOrDefault(emptyList())
            .filter { it.id == ID && it.tag?.startsWith("c") == true }
            .mapNotNull { it.tag.removePrefix("c").toLongOrNull() }.toSet()
    }

    /**
     * Which shown notifications to take down, given the inbox's unread conversations as the
     * server has them now ([unread], at most [limit]). First: gone for sure (the answer was
     * complete); second: missing from a full page, so they may only sit further down the list.
     */
    fun stale(shown: Set<Long>, unread: List<ConversationSummary>, limit: Int): Pair<Set<Long>, Set<Long>> {
        val missing = shown - unread.map { it.id }.toSet()
        return if (unread.size < limit) missing to emptySet() else emptySet<Long>() to missing
    }

    /** Whether a conversation still deserves its notification: unread and in the inbox. */
    fun stillNew(c: ConversationSummary, inboxLabel: Long?): Boolean =
        c.unread && (inboxLabel == null || inboxLabel in c.labelIds)

    /**
     * Take down notifications for conversations read, archived or deleted elsewhere. One request
     * for the inbox's unread conversations, and one per notification that is not among them on a
     * full page. Quiet on failure (they stay up until the next look).
     */
    suspend fun reconcile(g: AppGraph) {
        val shown = shown(g.context)
        if (shown.isEmpty()) return
        val page = catching { g.api.conversations(view = "inbox", q = "is:unread", limit = RECONCILE_LIMIT) }.getOrNull() ?: return
        val (gone, unsure) = stale(shown, page.items.filter { it.unread }, RECONCILE_LIMIT)
        val inbox = g.mail.roleLabel("inbox")?.id
        val alsoGone = unsure.take(10).filter { id ->
            val c = catching { g.api.conversation(id).conversation }.getOrElse { e ->
                // Deleted for good: its notification goes too.
                return@filter e is io.github.murtazanasir.mailr.data.api.ApiException && e.code == 404
            }
            !stillNew(c, inbox)
        }
        val all = gone + alsoGone
        if (all.isNotEmpty()) cancel(g.context, all.toList())
    }

    private const val RECONCILE_LIMIT = 50

    // ---------------- actions ----------------

    const val ARCHIVE = "io.github.murtazanasir.mailr.MAIL_ARCHIVE"
    const val READ = "io.github.murtazanasir.mailr.MAIL_READ"
    const val REPLY = "io.github.murtazanasir.mailr.MAIL_REPLY"
    const val EXTRA_ID = "conversation"
    const val KEY_REPLY = "reply_text"

    /** The buttons on a new-mail notification. Each needs the phone unlocked: from the lock screen,
     *  Archive and Mark read would let anyone holding the phone hide new mail from its owner. */
    fun actions(context: Context, id: Long): List<NotificationCompat.Action> {
        fun intent(what: String) = PendingIntent.getBroadcast(context, (what + id).hashCode(),
            Intent(context, MailActionReceiver::class.java).setAction(what).putExtra(EXTRA_ID, id),
            // Reply's intent receives the typed text, so it must stay mutable (RemoteInput).
            (if (what == REPLY) PendingIntent.FLAG_MUTABLE else PendingIntent.FLAG_IMMUTABLE) or PendingIntent.FLAG_UPDATE_CURRENT)
        val icon = R.drawable.ic_launcher_monochrome
        return listOf(
            NotificationCompat.Action.Builder(icon, context.getString(R.string.sync_action_archive), intent(ARCHIVE))
                .setSemanticAction(NotificationCompat.Action.SEMANTIC_ACTION_ARCHIVE).setShowsUserInterface(false)
                .setAuthenticationRequired(true).build(),
            NotificationCompat.Action.Builder(icon, context.getString(R.string.sync_action_mark_read), intent(READ))
                .setSemanticAction(NotificationCompat.Action.SEMANTIC_ACTION_MARK_AS_READ).setShowsUserInterface(false)
                .setAuthenticationRequired(true).build(),
            NotificationCompat.Action.Builder(icon, context.getString(R.string.sync_action_reply), intent(REPLY))
                .addRemoteInput(RemoteInput.Builder(KEY_REPLY).setLabel(context.getString(R.string.sync_action_reply)).build())
                .setSemanticAction(NotificationCompat.Action.SEMANTIC_ACTION_REPLY)
                .setAllowGeneratedReplies(false).setShowsUserInterface(false)
                .setAuthenticationRequired(true).build(),
        )
    }

    /** ACT mail.archive @android.notification, ACT mail.read @android.notification (android.notification): through the repository, so offline they queue. */
    suspend fun act(g: AppGraph, id: Long, action: String): Boolean = try {
        g.mail.act(listOf(id), action)
        true
    } catch (e: Exception) {
        false
    }

    /**
     * ACT mail.reply @android.notification (android.notification): the reply goes to the newest message, with the
     * server's prefill (recipients, subject, quote) and the typed text on top. The server holds
     * it for the undo window like any send. The conversation is then marked read.
     */
    suspend fun reply(g: AppGraph, id: Long, text: String, key: String = UUID.randomUUID().toString()): String? =
        try {
            replyOnce(g, id, text, key)
        } catch (e: Exception) {
            e.userMessage()
        }

    /**
     * One attempt of [reply]; a failure is thrown. [key] is the reply's Idempotency-Key, the same
     * for every attempt, so an attempt whose answer was lost and the next one send it once.
     */
    suspend fun replyOnce(g: AppGraph, id: Long, text: String, key: String): String? {
        if (text.isBlank()) return AppText.get(R.string.sync_reply_empty)
        val last = g.api.conversation(id).messages.lastOrNull { !it.draft } ?: return AppText.get(R.string.sync_reply_nothing)
        val prefill = g.api.replyPrefill(last.id, "reply")
        g.compose.send(replyPayload(prefill, text), null, null, key)
        catching { g.mail.act(listOf(id), "read") }
        cancel(g.context, listOf(id))
        return null
    }

    /** A failure worth another attempt: the network, or the server for a moment. */
    fun retryable(e: Exception): Boolean =
        e is IOException || e is ApiException && (e.code == 429 || e.code in 500..599)

    const val MAX_REPLY_ATTEMPTS = 5

    /**
     * Send a reply typed in a notification in the background: one job per reply, under its own
     * key, that tries again (with the same key) while the network or the server fails.
     */
    fun enqueueReply(context: Context, id: Long, text: String, key: String = UUID.randomUUID().toString()) {
        WorkManager.getInstance(context).enqueueUniqueWork(
            "reply-$key", ExistingWorkPolicy.KEEP,
            OneTimeWorkRequestBuilder<ReplyWorker>()
                .setInputData(workDataOf(EXTRA_ID to id, KEY_REPLY to text, KEY_IDEMPOTENCY to key))
                .setConstraints(Constraints.Builder().setRequiredNetworkType(NetworkType.CONNECTED).build())
                .setExpedited(OutOfQuotaPolicy.RUN_AS_NON_EXPEDITED_WORK_REQUEST)
                .setBackoffCriteria(BackoffPolicy.EXPONENTIAL, 10, TimeUnit.SECONDS).build(),
        )
    }

    const val KEY_IDEMPOTENCY = "idempotency_key"

    /**
     * The prefill with [text] typed at its top, as the composer would have it: the server's
     * signature, quote or forwarded history stay below exactly as the server wrote them.
     */
    fun replyPayload(prefill: ComposePayload, text: String): ComposePayload {
        val typed = ComposeBody.toHtml(ComposeBody.ofText(text.trim()))
        val rest = prefill.html ?: prefill.text?.let { ComposeBody.toHtml(ComposeBody.ofText(it)) } ?: ""
        return prefill.copy(html = typed + rest, text = null)
    }

    /** A reply that could not be sent: the notification says so and keeps the text to copy. */
    fun replyFailed(context: Context, id: Long, text: String, why: String) {
        val n = NotificationCompat.Builder(context, Channels.MAIL)
            .setSmallIcon(R.drawable.ic_launcher_monochrome)
            .setContentTitle(context.getString(R.string.sync_reply_not_sent))
            .setContentText(why)
            .setStyle(NotificationCompat.BigTextStyle().bigText("$why\n\n$text"))
            .setVisibility(NotificationCompat.VISIBILITY_PRIVATE)
            .setOnlyAlertOnce(true)
            .setAutoCancel(true)
            .setContentIntent(PendingIntent.getActivity(context, id.toInt(),
                io.github.murtazanasir.mailr.ui.util.InternalLinks.mark(context,
                    Intent(context, io.github.murtazanasir.mailr.ui.MainActivity::class.java).setAction(Intent.ACTION_VIEW)
                        .setData(android.net.Uri.parse("mailr://conversation/$id")))
                    .addFlags(Intent.FLAG_ACTIVITY_NEW_TASK or Intent.FLAG_ACTIVITY_CLEAR_TOP),
                PendingIntent.FLAG_IMMUTABLE or PendingIntent.FLAG_UPDATE_CURRENT))
            .build()
        io.github.murtazanasir.mailr.util.Notify.post(context, tag(id), ID, n)
    }
}

class MailActionReceiver : BroadcastReceiver() {
    override fun onReceive(context: Context, intent: Intent) {
        val id = intent.getLongExtra(MailNotifications.EXTRA_ID, -1).takeIf { it >= 0 } ?: return
        val g = (context.applicationContext as MailrApp).graph
        when (intent.action) {
            MailNotifications.ARCHIVE -> async { MailNotifications.act(g, id, "archive") }
            MailNotifications.READ -> async { MailNotifications.act(g, id, "read") }
            MailNotifications.REPLY -> {
                val text = RemoteInput.getResultsFromIntent(intent)?.getCharSequence(MailNotifications.KEY_REPLY)?.toString().orEmpty()
                MailNotifications.enqueueReply(context, id, text)
            }
        }
    }
}

/** A notification's reply ([MailNotifications.enqueueReply]): every attempt sends under the same key. */
class ReplyWorker(ctx: Context, params: WorkerParameters) : CoroutineWorker(ctx, params) {
    override suspend fun doWork(): Result {
        val id = inputData.getLong(MailNotifications.EXTRA_ID, -1).takeIf { it >= 0 } ?: return Result.success()
        val text = inputData.getString(MailNotifications.KEY_REPLY).orEmpty()
        val key = inputData.getString(MailNotifications.KEY_IDEMPOTENCY) ?: return Result.success()
        val g = (applicationContext as MailrApp).graph
        val why = try {
            MailNotifications.replyOnce(g, id, text, key)
        } catch (e: Exception) {
            if (MailNotifications.retryable(e) && runAttemptCount + 1 < MailNotifications.MAX_REPLY_ATTEMPTS) return Result.retry()
            e.userMessage()
        }
        if (why != null) MailNotifications.replyFailed(applicationContext, id, text, why)
        return Result.success()
    }
}
