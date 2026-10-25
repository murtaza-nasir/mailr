package io.github.murtazanasir.mailr.data.sync

import io.github.murtazanasir.mailr.util.catching

import android.Manifest
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent
import android.content.pm.PackageManager
import androidx.core.app.NotificationCompat
import androidx.core.app.NotificationManagerCompat
import androidx.core.content.ContextCompat
import androidx.work.BackoffPolicy
import androidx.work.Constraints
import androidx.work.CoroutineWorker
import androidx.work.ExistingPeriodicWorkPolicy
import androidx.work.ExistingWorkPolicy
import androidx.work.NetworkType
import androidx.work.OneTimeWorkRequestBuilder
import androidx.work.OutOfQuotaPolicy
import androidx.work.PeriodicWorkRequestBuilder
import androidx.work.WorkManager
import androidx.work.WorkerParameters
import io.github.murtazanasir.mailr.AppGraph
import io.github.murtazanasir.mailr.MailrApp
import io.github.murtazanasir.mailr.R
import io.github.murtazanasir.mailr.data.api.Approval
import io.github.murtazanasir.mailr.data.api.AssistantRun
import io.github.murtazanasir.mailr.data.api.ConversationSummary
import io.github.murtazanasir.mailr.data.reminders.async
import io.github.murtazanasir.mailr.data.settings.NotifyMode
import io.github.murtazanasir.mailr.ui.MainActivity
import kotlinx.coroutines.flow.first
import kotlinx.coroutines.sync.withLock
import java.util.concurrent.TimeUnit
import io.github.murtazanasir.mailr.i18n.AppText

object Channels {
    const val MAIL = "mail"
    const val APPROVALS = "approvals"
    const val SENDING = "sending"
    const val ASSISTANT = "assistant"

    fun create(context: Context) {
        val nm = context.getSystemService(NotificationManager::class.java)
        nm.createNotificationChannels(listOf(
            NotificationChannel(MAIL, context.getString(R.string.channel_mail), NotificationManager.IMPORTANCE_DEFAULT)
                .apply { description = context.getString(R.string.channel_mail_desc) },
            NotificationChannel(APPROVALS, context.getString(R.string.channel_approvals), NotificationManager.IMPORTANCE_HIGH)
                .apply { description = context.getString(R.string.channel_approvals_desc) },
            NotificationChannel(SENDING, context.getString(R.string.channel_sending), NotificationManager.IMPORTANCE_DEFAULT)
                .apply { description = context.getString(R.string.channel_sending_desc) },
            NotificationChannel(ASSISTANT, context.getString(R.string.channel_assistant), NotificationManager.IMPORTANCE_DEFAULT)
                .apply { description = context.getString(R.string.channel_assistant_desc) },
        ))
    }
}

object Work {
    private val online = Constraints.Builder().setRequiredNetworkType(NetworkType.CONNECTED).build()

    /** Replay the offline queue as soon as there is a network. */
    fun drainOutbox(context: Context) {
        WorkManager.getInstance(context).enqueueUniqueWork(
            "outbox", ExistingWorkPolicy.KEEP,
            OneTimeWorkRequestBuilder<OutboxWorker>().setConstraints(online)
                .setExpedited(OutOfQuotaPolicy.RUN_AS_NON_EXPEDITED_WORK_REQUEST)
                .setBackoffCriteria(BackoffPolicy.EXPONENTIAL, 15, TimeUnit.SECONDS).build(),
        )
    }

    /**
     * Background catch-up and notifications: every 15 minutes (the platform minimum), or every
     * 60 while push works (data/push), when the poll is only a fallback for a lost message.
     */
    fun schedulePeriodicSync(context: Context, minutes: Long = 15) {
        WorkManager.getInstance(context).enqueueUniquePeriodicWork(
            "sync", ExistingPeriodicWorkPolicy.UPDATE,
            PeriodicWorkRequestBuilder<SyncWorker>(minutes.coerceAtLeast(15), TimeUnit.MINUTES).setConstraints(online).build(),
        )
    }

    fun cancelAll(context: Context) {
        WorkManager.getInstance(context).cancelAllWork()
    }
}

class OutboxWorker(ctx: Context, params: WorkerParameters) : CoroutineWorker(ctx, params) {
    override suspend fun doWork(): Result {
        val g = (applicationContext as MailrApp).graph
        if (g.sessions.session.value == null) return Result.success()
        val calendarOps = g.db.pending().calendarCount() > 0
        // drainOutbox keeps a running drain (KEEP), so a change queued while it ran gets no run of
        // its own: drain again while new changes are left (a few rounds; the next wake takes the rest).
        for (round in 0 until 5) {
            when (g.mail.drain()) {
                // Something still waits for the network or a backoff.
                io.github.murtazanasir.mailr.data.repo.MailRepository.DrainResult.RETRY -> return Result.retry()
                // The session ended (401): nothing is dropped; signing in again starts the queue.
                io.github.murtazanasir.mailr.data.repo.MailRepository.DrainResult.PAUSED -> return Result.success()
                io.github.murtazanasir.mailr.data.repo.MailRepository.DrainResult.DONE -> Unit
            }
            if (g.db.pending().queued().isEmpty()) break
        }
        // Queued calendar/task changes have landed: take the server's view (clears dirty rows).
        if (calendarOps) catching { g.calendar.sync() }
        return Result.success()
    }
}

class SyncWorker(ctx: Context, params: WorkerParameters) : CoroutineWorker(ctx, params) {
    override suspend fun doWork(): Result {
        val g = (applicationContext as MailrApp).graph
        if (g.sessions.session.value == null) return Result.success()
        return try {
            if (backgroundSync(g)) Result.success() else Result.retry()
        } catch (e: kotlinx.coroutines.CancellationException) {
            throw e
        } catch (e: Exception) {
            Result.retry()
        }
    }
}

/**
 * The periodic background sync: the offline queue, notes and tables, and (in the background) the
 * notification polls. False when new mail could not be read (the worker retries).
 */
suspend fun backgroundSync(g: AppGraph): Boolean {
    g.mail.drainPending()
    // Notes and tables: the replay queue, then the delta. With the Notes app installed it
    // pulls them itself; this app only sends its own queued changes.
    catching { g.notes.repo.drain(); if (g.notesPullHere()) g.notes.repo.pull() }
    val prefs = g.settings.prefs.first()
    if (!g.foreground.value) {
        if (!pollNewMail(g, prefs.mailNotify())) return false
        if (prefs.notifyApprovals) pollApprovals(g)
        notifyAssistant(g)
        if (prefs.mailNotify() != NotifyMode.OFF) pollNudges(g)
    }
    g.followBackgroundCadence()
    return true
}

/**
 * One poll and one push message never notify the same thing twice: both read and write the
 * "already notified" lists under this lock.
 */
val notifyLock = kotlinx.coroutines.sync.Mutex()

private const val KV_NOTIFIED = "notified"

/**
 * New mail since the last look, per Settings → New mail: one notification per conversation
 * (tag "c<id>"), remembered in the "notified" list. False when the server could not be read.
 * Called by the periodic poll and by a push message (data/push).
 */
suspend fun pollNewMail(g: AppGraph, mode: NotifyMode): Boolean {
    if (mode == NotifyMode.OFF) return true
    return notifyLock.withLock {
        val page = catching { g.api.conversations(
            view = "inbox",
            section = if (mode == NotifyMode.IMPORTANT) "important" else null,
            q = when (mode) {
                NotifyMode.PRIMARY -> "category:primary is:unread"
                NotifyMode.VIP -> "is:vip is:unread"  // VIP senders: Settings → Mail → Inbox on the web
                else -> "is:unread"
            },
            limit = 10,
        ) }.getOrElse { return@withLock false }
        val seen = notifiedConversations(g)
        val fresh = page.items.filter { it.unread && it.id !in seen && !it.muted }
        if (fresh.isNotEmpty()) Notifier.newMail(g.context, fresh)
        g.mail.putKv(KV_NOTIFIED, (page.items.map { it.id } + seen).distinct().take(200).joinToString(","))
        true
    }.also {
        // What was read or archived on another device leaves the shade.
        catching { MailNotifications.reconcile(g) }
    }
}

suspend fun notifiedConversations(g: AppGraph): Set<Long> =
    g.mail.kv(KV_NOTIFIED)?.split(',')?.mapNotNull { it.toLongOrNull() }?.toSet() ?: emptySet()

/** Results the assistant delivered as notifications (standing orders and routines). */
suspend fun notifyAssistant(g: AppGraph) = notifyLock.withLock {
    val runs = catching { g.api.assistantRuns() }.getOrDefault(emptyList())
    val last = g.mail.kv("assistant_seen_run")?.toLongOrNull()
    if (last == null) {  // first poll after install: remember where we are, show nothing old
        runs.maxOfOrNull { it.id }?.let { g.mail.putKv("assistant_seen_run", it.toString()) }
        return@withLock
    }
    val fresh = runs.filter { it.id > last && it.status == "done" && it.delivered.any { d -> d == "notify" } }
    if (fresh.isNotEmpty()) Notifier.assistant(g.context, fresh)
    runs.maxOfOrNull { it.id }?.let { if (it > last) g.mail.putKv("assistant_seen_run", it.toString()) }
}

/**
 * What a background poll notifies, apart from the worker so tests can run it: which items are
 * new since the last poll, and the "already notified" list to store for the next one.
 */
object NotifyDecisions {
    /** ACT mail.follow_up @android.notification: the inbox search for conversations a follow-up brought back with no reply. */
    const val NUDGED_QUERY = "is:nudged"

    /** A nudge is new until notified; a later nudge on the same conversation is a new one. */
    fun nudgeKey(c: ConversationSummary) = "${c.id}@${c.nudgedAt}"

    fun freshNudges(items: List<ConversationSummary>, seen: String?): Pair<List<ConversationSummary>, String> {
        val before = seen?.split(',')?.toSet() ?: emptySet()
        val nudged = items.filter { it.nudgedAt != null }
        val fresh = nudged.filter { nudgeKey(it) !in before }
        return fresh to (nudged.map(::nudgeKey) + before).distinct().take(100).joinToString(",")
    }

    /** One approval's identity; the legacy outbox queue numbers its rows apart, hence the "o". */
    fun approvalKey(a: Approval) = if (a.outboxId != null) "o${a.id}" else "${a.id}"

    /** [post]: pending and not notified yet. [cancel]: notified before and no longer pending (decided elsewhere, expired). */
    data class ApprovalPlan(val post: List<Approval>, val cancel: List<String>, val seen: String)

    fun approvals(pending: List<Approval>, seen: String?): ApprovalPlan {
        val before = seen?.split(',')?.filter { it.isNotBlank() }?.toSet() ?: emptySet()
        val now = pending.map(::approvalKey)
        return ApprovalPlan(pending.filter { approvalKey(it) !in before }, (before - now.toSet()).toList(), now.joinToString(","))
    }
}

private const val KV_NUDGES = "nudges_notified"
private const val KV_APPROVALS = "approvals_notified"

/** ACT mail.follow_up (android.notification): "No reply from Dana", once per nudge. */
suspend fun pollNudges(g: AppGraph) {
    val page = catching { g.api.conversations(view = "inbox", q = NotifyDecisions.NUDGED_QUERY, limit = 10) }.getOrNull() ?: return
    val (fresh, seen) = NotifyDecisions.freshNudges(page.items, g.mail.kv(KV_NUDGES))
    if (fresh.isNotEmpty()) Notifier.nudges(g.context, fresh)
    g.mail.putKv(KV_NUDGES, seen)
}

/**
 * ACT approval.approve @android.notification, ACT approval.deny @android.notification (android.notification): one notification per pending
 * approval. False when the list could not be read. Called by the poll and by a push message.
 */
suspend fun pollApprovals(g: AppGraph): Boolean = notifyLock.withLock {
    val pending = catching { g.api.approvals() }.getOrElse { return@withLock false }
    val plan = NotifyDecisions.approvals(pending, g.mail.kv(KV_APPROVALS))
    plan.post.forEach { Notifier.approval(g.context, it, summary = false) }
    plan.cancel.forEach { Notifier.cancelApproval(g.context, it, summary = false) }
    // Any other approval notification whose approval is no longer pending (an older process's).
    Notifier.cancelApprovalsExcept(g.context, pending.map(NotifyDecisions::approvalKey).toSet())
    g.mail.putKv(KV_APPROVALS, plan.seen)
    true
}

/**
 * ACT approval.sync @android.notification: approvals that left pending on another device (the
 * server's `approval.decided` push or event). Their notifications go at once and leave the
 * "already notified" list, and the group summary follows. A message that could not be decrypted
 * names nothing trusted: then only [ApprovalsTracker.refresh]'s read of the list decides.
 */
suspend fun approvalsDecided(g: AppGraph, keys: Collection<String>) = notifyLock.withLock {
    if (keys.isEmpty()) return@withLock
    keys.forEach { Notifier.cancelApproval(g.context, it, summary = false) }
    Notifier.approvalSummary(g.context)
    val left = notifiedApprovals(g) - keys.toSet()
    g.mail.putKv(KV_APPROVALS, left.joinToString(","))
}

suspend fun notifiedApprovals(g: AppGraph): Set<String> =
    g.mail.kv(KV_APPROVALS)?.split(',')?.filter { it.isNotBlank() }?.toSet() ?: emptySet()

/**
 * The Approve or Deny button of an approval notification: POST /approvals/{id}/decide
 * {"decision", "scope": "once"} (the outbox queue for its own rows), then the notification goes.
 * On failure the notification stays, so the user can answer again or open the app.
 */
suspend fun decideFromNotification(g: AppGraph, id: Long, outboxId: Long?, approve: Boolean): Boolean {
    val a = Approval(id = id, outboxId = outboxId)
    return try {
        g.api.decide(a, approve, "once")
        Notifier.cancelApproval(g.context, NotifyDecisions.approvalKey(a))
        true
    } catch (e: Exception) {
        false
    }
}

class ApprovalActionReceiver : BroadcastReceiver() {
    override fun onReceive(context: Context, intent: Intent) {
        val approve = when (intent.action) { APPROVE -> true; DENY -> false; else -> return }
        val id = intent.getLongExtra(EXTRA_ID, -1).takeIf { it >= 0 } ?: return
        val outbox = intent.getLongExtra(EXTRA_OUTBOX, -1).takeIf { it >= 0 }
        val g = (context.applicationContext as MailrApp).graph
        async { decideFromNotification(g, id, outbox, approve) }
    }

    companion object {
        const val APPROVE = "io.github.murtazanasir.mailr.APPROVAL_APPROVE"
        const val DENY = "io.github.murtazanasir.mailr.APPROVAL_DENY"
        const val EXTRA_ID = "approval"
        const val EXTRA_OUTBOX = "outbox"
    }
}

object Notifier {
    private fun allowed(context: Context) = ContextCompat.checkSelfPermission(context,
        Manifest.permission.POST_NOTIFICATIONS) == PackageManager.PERMISSION_GRANTED

    private fun open(context: Context, uri: String, code: Int): PendingIntent = PendingIntent.getActivity(
        context, code,
        io.github.murtazanasir.mailr.ui.util.InternalLinks.mark(context,
            Intent(context, MainActivity::class.java).setAction(Intent.ACTION_VIEW).setData(android.net.Uri.parse(uri)))
            .addFlags(Intent.FLAG_ACTIVITY_NEW_TASK or Intent.FLAG_ACTIVITY_CLEAR_TOP),
        PendingIntent.FLAG_IMMUTABLE or PendingIntent.FLAG_UPDATE_CURRENT,
    )

    fun assistant(context: Context, runs: List<AssistantRun>) {
        if (!allowed(context)) return
        val nm = NotificationManagerCompat.from(context)
        runs.take(5).forEach { r ->
            val text = r.output.lineSequence().map { it.replace(Regex("\\[(\\d+)\\]\\(mailr-cite:\\d+\\)"), "").trim(' ', '-', '*', '#') }
                .firstOrNull { it.isNotBlank() } ?: context.getString(R.string.sync_assistant_done)
            val uri = r.conversationId?.let { "mailr://conversation/$it" } ?: "mailr://inbox"
            val n = NotificationCompat.Builder(context, Channels.ASSISTANT)
                .setSmallIcon(R.drawable.ic_launcher_monochrome)
                .setContentTitle(if (r.orderBuiltin == "forwarded_notes") context.getString(R.string.orders_builtin_name) else r.orderName ?: io.github.murtazanasir.mailr.i18n.AssistantName.now())
                .setContentText(text)
                .setStyle(NotificationCompat.BigTextStyle().bigText(r.output.replace(Regex("\\[(\\d+)\\]\\(mailr-cite:\\d+\\)"), "").take(900)))
                .setGroup("assistant")
                .setAutoCancel(true)
                .setContentIntent(open(context, uri, (r.id % Int.MAX_VALUE).toInt()))
                .build()
            io.github.murtazanasir.mailr.util.Notify.post(context, "a${r.id}", 3, n)
        }
    }

    fun nudges(context: Context, items: List<ConversationSummary>) {
        if (!allowed(context)) return
        val nm = NotificationManagerCompat.from(context)
        items.take(5).forEach { c ->
            val who = c.participants.firstOrNull { !it.me }?.let { io.github.murtazanasir.mailr.data.api.SafeText.clean(it.name?.ifBlank { null } ?: it.email) }
            val n = NotificationCompat.Builder(context, Channels.MAIL)
                .setSmallIcon(R.drawable.ic_launcher_monochrome)
                .setContentTitle(if (who != null) context.getString(R.string.sync_no_reply_from, who) else context.getString(R.string.sync_no_reply_yet))
                .setContentText(c.subject.ifBlank { context.getString(R.string.sync_no_subject) })
                .setGroup("nudge")
                .setAutoCancel(true)
                .setContentIntent(open(context, "mailr://conversation/${c.id}", c.id.toInt() xor 0x4e000000))
                .build()
            io.github.murtazanasir.mailr.util.Notify.post(context, "n${c.id}", 4, n)
        }
    }

    fun newMail(context: Context, items: List<ConversationSummary>) {
        if (!allowed(context)) return
        val nm = NotificationManagerCompat.from(context)
        items.take(5).forEach { c ->
            val from = c.participants.firstOrNull { !it.me }?.let { io.github.murtazanasir.mailr.data.api.SafeText.clean(it.name?.ifBlank { null } ?: it.email) }
                ?: context.getString(R.string.sync_new_message)
            val n = NotificationCompat.Builder(context, Channels.MAIL)
                .setSmallIcon(R.drawable.ic_launcher_monochrome)
                .setContentTitle(from)
                // The header says it is mail, so a sender named like a mailr notice reads as a sender.
                .setSubText(MAIL_SUBTEXT)
                .setPublicVersion(mailPublic(context))
                .setContentText(c.subject.ifBlank { context.getString(R.string.sync_no_subject) })
                .setStyle(NotificationCompat.BigTextStyle().bigText("${c.subject}\n${c.snippet}"))
                .setGroup("mail")
                .setAutoCancel(true)
                .setCategory(NotificationCompat.CATEGORY_EMAIL)
                // A push message may have posted this conversation first (newMailHint): replace it quietly.
                .setOnlyAlertOnce(true)
                .setContentIntent(open(context, "mailr://conversation/${c.id}", c.id.toInt()))
                // Archive, Mark read and Reply, without opening the app.
                .apply { MailNotifications.actions(context, c.id).forEach { addAction(it) } }
                .build()
            io.github.murtazanasir.mailr.util.Notify.post(context, MailNotifications.tag(c.id), MailNotifications.ID, n)
        }
        mailSummary(context)
    }

    /**
     * The new-mail group's summary: "3 new conversations" with one line per conversation (InboxStyle),
     * counted from the notifications showing now, so mail that arrived one push at a time is grouped
     * too. Under two, there is no summary. It never alerts on its own (the newest child does).
     */
    fun mailSummary(context: Context) {
        val nm = NotificationManagerCompat.from(context)
        val shown = catching { context.getSystemService(android.app.NotificationManager::class.java).activeNotifications.toList() }
            .getOrDefault(emptyList())
            .filter { it.id == MailNotifications.ID && it.tag?.startsWith("c") == true }
            .sortedByDescending { it.postTime }
        if (shown.size < 2) {
            catching { nm.cancel(MailNotifications.SUMMARY_TAG, 0) }
            return
        }
        val title = context.resources.getQuantityString(R.plurals.sync_new_conversations, shown.size, shown.size)
        val style = NotificationCompat.InboxStyle().setBigContentTitle(title)
        shown.take(6).forEach { sbn ->
            val x = sbn.notification.extras
            val from = x.getCharSequence(android.app.Notification.EXTRA_TITLE)?.toString().orEmpty()
            val subject = x.getCharSequence(android.app.Notification.EXTRA_TEXT)?.toString().orEmpty()
            style.addLine(listOf(from, subject).filter { it.isNotBlank() }.joinToString(" · "))
        }
        if (shown.size > 6) style.setSummaryText(context.getString(R.string.sync_summary_more, shown.size - 6))
        io.github.murtazanasir.mailr.util.Notify.post(context, MailNotifications.SUMMARY_TAG, 0, NotificationCompat.Builder(context, Channels.MAIL)
            .setSmallIcon(R.drawable.ic_launcher_monochrome)
            .setContentTitle(title)
            .setSubText(MAIL_SUBTEXT)
            .setStyle(style)
            .setPublicVersion(mailPublic(context))
            .setNumber(shown.size)
            .setCategory(NotificationCompat.CATEGORY_EMAIL)
            .setGroup("mail").setGroupSummary(true)
            .setGroupAlertBehavior(NotificationCompat.GROUP_ALERT_CHILDREN)
            .setOnlyAlertOnce(true).setAutoCancel(true)
            .setContentIntent(open(context, "mailr://inbox", 0)).build())
    }

    /** An approval's notification: tag "approval-" + [NotifyDecisions.approvalKey], id [APPROVAL_ID]. */
    fun approvalTag(key: String) = "approval-$key"
    const val APPROVAL_ID = 2

    /**
     * One pending approval: its preview's title and lines, with Allow once and Deny. Both buttons
     * need the device unlocked, so nobody can answer from the lock screen. A tap opens the
     * Approvals screen at this approval (mailr://approvals/<key>) and leaves it pending; only an
     * answer ends it.
     */
    fun approval(context: Context, a: Approval, summary: Boolean = true) {
        if (!allowed(context)) return
        val key = NotifyDecisions.approvalKey(a)
        // The display model's plain text (agent/present.py): the title, then one line per field.
        val d = a.shown
        val title = d?.title ?: a.preview.title.ifBlank { a.subject.ifBlank { context.getString(R.string.sync_agent_wants, a.actor.ifBlank { context.getString(R.string.sync_an_agent) }, a.action) } }
        val lines = (d?.text?.lines()?.drop(1) ?: a.preview.lines).filter { it.isNotBlank() }
        val style = NotificationCompat.InboxStyle().setBigContentTitle(title)
        lines.take(6).forEach { style.addLine(it) }
        if (a.actor.isNotBlank()) style.setSummaryText(a.actor)
        fun answer(what: String, label: String) = NotificationCompat.Action.Builder(R.drawable.ic_launcher_monochrome, label,
            PendingIntent.getBroadcast(context, (what + key).hashCode(),
                Intent(context, ApprovalActionReceiver::class.java).setAction(what)
                    .putExtra(ApprovalActionReceiver.EXTRA_ID, a.id).putExtra(ApprovalActionReceiver.EXTRA_OUTBOX, a.outboxId ?: -1L),
                PendingIntent.FLAG_IMMUTABLE or PendingIntent.FLAG_UPDATE_CURRENT))
            .setAuthenticationRequired(true)
            .build()
        val n = NotificationCompat.Builder(context, Channels.APPROVALS)
            .setSmallIcon(R.drawable.ic_launcher_monochrome)
            .setContentTitle(title)
            .setContentText(lines.firstOrNull() ?: context.getString(R.string.sync_waiting_ok))
            .setStyle(style)
            .setPriority(NotificationCompat.PRIORITY_HIGH)
            .setOnlyAlertOnce(true)
            .setGroup(APPROVAL_GROUP)
            .setGroupAlertBehavior(NotificationCompat.GROUP_ALERT_CHILDREN)
            .setAutoCancel(false)
            .setVisibility(NotificationCompat.VISIBILITY_PRIVATE)
            .setPublicVersion(NotificationCompat.Builder(context, Channels.APPROVALS)
                .setSmallIcon(R.drawable.ic_launcher_monochrome)
                .setContentTitle(context.getString(R.string.sync_agent_waiting_ok)).build())
            .setContentIntent(open(context, "mailr://approvals/$key", ("approvals" + key).hashCode()))
            .addAction(answer(ApprovalActionReceiver.APPROVE, context.getString(R.string.sync_allow_once)))
            .addAction(answer(ApprovalActionReceiver.DENY, context.getString(R.string.sync_deny)))
            .build()
        io.github.murtazanasir.mailr.util.Notify.post(context, approvalTag(key), APPROVAL_ID, n)
        if (summary) approvalSummary(context)
    }

    const val APPROVAL_GROUP = "approvals"
    /** The approvals group's summary: same id as its children, a tag no approval key can make. */
    const val APPROVAL_SUMMARY_TAG = "approvals-summary"

    /** The approval notifications showing now, newest first (their keys, from the tag). */
    private fun shownApprovals(context: Context): List<android.service.notification.StatusBarNotification> {
        val nm = context.getSystemService(android.app.NotificationManager::class.java) ?: return emptyList()
        return catching { nm.activeNotifications.toList() }.getOrDefault(emptyList())
            .filter { it.id == APPROVAL_ID && it.tag?.startsWith("approval-") == true }
            .sortedByDescending { it.postTime }
    }

    /**
     * The approvals group's summary, built from the approval notifications showing: a line per
     * approval (its title) and their count. Posted again after every change, quietly; cancelled
     * only when no approval notification is left, because cancelling a summary also cancels its
     * children.
     */
    fun approvalSummary(context: Context) {
        val shown = shownApprovals(context)
        if (shown.isEmpty()) {
            NotificationManagerCompat.from(context).cancel(APPROVAL_SUMMARY_TAG, APPROVAL_ID)
            return
        }
        if (!allowed(context)) return
        val titles = shown.map { it.notification.extras.getCharSequence(android.app.Notification.EXTRA_TITLE)?.toString().orEmpty() }
            .filter { it.isNotBlank() }
        val count = context.resources.getQuantityString(R.plurals.sync_approvals_waiting, shown.size, shown.size)
        val style = NotificationCompat.InboxStyle().setBigContentTitle(count)
        titles.take(SUMMARY_LINES).forEach { style.addLine(it) }
        if (titles.size > SUMMARY_LINES) style.setSummaryText(AppText.plural(R.plurals.rem_more, titles.size - SUMMARY_LINES, titles.size - SUMMARY_LINES))
        val n = NotificationCompat.Builder(context, Channels.APPROVALS)
            .setSmallIcon(R.drawable.ic_launcher_monochrome)
            .setContentTitle(count)
            .setContentText(titles.firstOrNull() ?: context.getString(R.string.sync_waiting_ok))
            .setStyle(style)
            .setNumber(shown.size)
            .setGroup(APPROVAL_GROUP)
            .setGroupSummary(true)
            .setGroupAlertBehavior(NotificationCompat.GROUP_ALERT_CHILDREN)
            .setOnlyAlertOnce(true)
            .setSilent(true)
            .setAutoCancel(false)
            .setVisibility(NotificationCompat.VISIBILITY_PRIVATE)
            .setPublicVersion(NotificationCompat.Builder(context, Channels.APPROVALS)
                .setSmallIcon(R.drawable.ic_launcher_monochrome)
                .setContentTitle(count).build())
            .setContentIntent(open(context, "mailr://approvals", "approvals-summary".hashCode()))
            .build()
        io.github.murtazanasir.mailr.util.Notify.post(context, APPROVAL_SUMMARY_TAG, APPROVAL_ID, n)
    }

    private const val SUMMARY_LINES = 6

    /**
     * New mail from a push message when the inbox could not be read: the sender (or "Mailr")
     * and "New message", under the conversation's own tag, so the next poll replaces it with
     * the subject and preview without alerting again.
     */
    fun newMailHint(context: Context, conversationId: Long, title: String) {
        if (!allowed(context)) return
        val n = NotificationCompat.Builder(context, Channels.MAIL)
            .setSmallIcon(R.drawable.ic_launcher_monochrome)
            .setContentTitle(io.github.murtazanasir.mailr.data.api.SafeText.clean(title).ifBlank { "Mailr" })
            .setContentText(context.getString(R.string.sync_new_message))
            .setSubText(MAIL_SUBTEXT)
            .setPublicVersion(mailPublic(context))
            .setGroup("mail")
            .setAutoCancel(true)
            .setOnlyAlertOnce(true)
            .setCategory(NotificationCompat.CATEGORY_EMAIL)
            .setContentIntent(open(context, "mailr://conversation/$conversationId", conversationId.toInt()))
            .apply { MailNotifications.actions(context, conversationId).forEach { addAction(it) } }
            .build()
        io.github.murtazanasir.mailr.util.Notify.post(context, MailNotifications.tag(conversationId), MailNotifications.ID, n)
        mailSummary(context)
    }

    /** A message that could not be sent (tag "send-failed-<outbox id>"); a tap opens Scheduled. */
    fun sendFailed(context: Context, outboxId: Long, title: String, text: String) {
        if (!allowed(context)) return
        val n = NotificationCompat.Builder(context, Channels.SENDING)
            .setSmallIcon(R.drawable.ic_launcher_monochrome)
            .setContentTitle(title.ifBlank { context.getString(R.string.sync_message_not_sent) })
            .setContentText(text)
            .setStyle(NotificationCompat.BigTextStyle().bigText(text))
            .setCategory(NotificationCompat.CATEGORY_ERROR)
            .setAutoCancel(true)
            .setContentIntent(open(context, "mailr://scheduled", ("sf$outboxId").hashCode()))
            .build()
        io.github.murtazanasir.mailr.util.Notify.post(context, sendFailedTag(outboxId), SEND_FAILED_ID, n)
    }

    fun sendFailedTag(outboxId: Long) = "send-failed-$outboxId"

    /**
     * A mail account stopped because its provider refused the password (the server sends it once per
     * failure). A message that could not be decrypted shows fixed text. The tap opens Settings →
     * Accounts on the web, where the password is entered again.
     */
    fun accountSignin(context: Context, server: String, title: String, text: String) {
        if (!allowed(context)) return
        val body = text.ifBlank { context.getString(R.string.sync_account_reenter) }
        val open = PendingIntent.getActivity(context, ACCOUNT_ID,
            Intent(Intent.ACTION_VIEW, android.net.Uri.parse(server.trimEnd('/') + "/settings/mail/accounts")),
            PendingIntent.FLAG_IMMUTABLE or PendingIntent.FLAG_UPDATE_CURRENT)
        io.github.murtazanasir.mailr.util.Notify.post(context, ACCOUNT_TAG, ACCOUNT_ID, NotificationCompat.Builder(context, Channels.SENDING)
            .setSmallIcon(R.drawable.ic_launcher_monochrome)
            .setContentTitle(title.ifBlank { context.getString(R.string.sync_account_password) })
            .setContentText(body)
            .setStyle(NotificationCompat.BigTextStyle().bigText(body))
            .setCategory(NotificationCompat.CATEGORY_ERROR)
            .setAutoCancel(true)
            .setContentIntent(open)
            .build())
    }

    const val ACCOUNT_TAG = "account-signin"
    const val ACCOUNT_ID = 8

    /** The sub-text on every new-mail notification. */
    val MAIL_SUBTEXT: String get() = AppText.get(R.string.sync_new_mail)

    /** What a new-mail notification shows on a lock screen set to hide sensitive content. */
    private fun mailPublic(context: Context) = NotificationCompat.Builder(context, Channels.MAIL)
        .setSmallIcon(R.drawable.ic_launcher_monochrome).setContentTitle(context.getString(R.string.sync_new_mail)).build()
    const val SEND_FAILED_ID = 5

    /**
     * Settings → Push → Send test. The text is fixed: whoever knows the push address can post a
     * "test", so its own title and text are never shown.
     */
    fun pushTest(context: Context) {
        if (!allowed(context)) return
        io.github.murtazanasir.mailr.util.Notify.post(context, PUSH_TEST_TAG, PUSH_TEST_ID, NotificationCompat.Builder(context, Channels.MAIL)
            .setSmallIcon(R.drawable.ic_launcher_monochrome)
            .setContentTitle("Mailr")
            .setContentText(context.getString(R.string.sync_push_test))
            .setAutoCancel(true)
            .setContentIntent(open(context, "mailr://inbox", PUSH_TEST_ID))
            .build())
    }

    const val PUSH_TEST_TAG = "push-test"
    const val PUSH_TEST_ID = 6

    fun cancelApproval(context: Context, key: String, summary: Boolean = true) {
        NotificationManagerCompat.from(context).cancel(approvalTag(key), APPROVAL_ID)
        if (summary) approvalSummary(context)
    }

    /**
     * Cancel every approval notification whose key is not in [pending]: the approval was answered
     * on another device, in the app, or it expired. Called after each read of the pending list.
     */
    fun cancelApprovalsExcept(context: Context, pending: Set<String>) {
        shownApprovals(context)
            .map { it.tag.removePrefix("approval-") }
            .filter { it !in pending }
            .forEach { cancelApproval(context, it, summary = false) }
        approvalSummary(context)
    }
}
