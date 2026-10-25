package io.github.murtazanasir.mailr.data.reminders

import io.github.murtazanasir.mailr.util.catching

import android.Manifest
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent
import android.content.pm.PackageManager
import android.media.AudioAttributes
import android.media.RingtoneManager
import android.net.Uri
import androidx.core.app.NotificationCompat
import androidx.core.app.NotificationManagerCompat
import androidx.core.content.ContextCompat
import io.github.murtazanasir.mailr.MailrApp
import io.github.murtazanasir.mailr.R
import io.github.murtazanasir.mailr.data.db.ReminderEntity
import io.github.murtazanasir.mailr.i18n.AppText
import io.github.murtazanasir.mailr.ui.calendar.ReminderActivity
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.flow.first
import kotlinx.coroutines.launch
import kotlinx.coroutines.withTimeoutOrNull

object ReminderChannels {
    const val EVENTS = "event_reminders"
    const val TASKS = "task_reminders"
    const val MISSED = "missed_reminders"
    const val AGENDA = "daily_agenda"

    fun create(context: Context) {
        val nm = context.getSystemService(NotificationManager::class.java)
        val sound = RingtoneManager.getDefaultUri(RingtoneManager.TYPE_NOTIFICATION)
        val attrs = AudioAttributes.Builder().setUsage(AudioAttributes.USAGE_NOTIFICATION_EVENT)
            .setContentType(AudioAttributes.CONTENT_TYPE_SONIFICATION).build()
        fun high(id: String, name: Int, desc: Int) = NotificationChannel(id, context.getString(name), NotificationManager.IMPORTANCE_HIGH).apply {
            description = context.getString(desc)
            setSound(sound, attrs)
            enableVibration(true)
            vibrationPattern = longArrayOf(0, 250, 150, 250)
            enableLights(true)
            setShowBadge(true)
            lockscreenVisibility = android.app.Notification.VISIBILITY_PRIVATE
        }
        nm.createNotificationChannels(listOf(
            high(EVENTS, R.string.channel_event_reminders, R.string.channel_event_reminders_desc),
            high(TASKS, R.string.channel_task_reminders, R.string.channel_task_reminders_desc),
            NotificationChannel(MISSED, context.getString(R.string.channel_missed_reminders), NotificationManager.IMPORTANCE_DEFAULT)
                .apply { description = context.getString(R.string.channel_missed_reminders_desc) },
            NotificationChannel(AGENDA, context.getString(R.string.channel_agenda), NotificationManager.IMPORTANCE_LOW)
                .apply { description = context.getString(R.string.channel_agenda_desc) },
        ))
    }
}

/**
 * Posts reminder notifications.
 *
 * - Every reminder has a stable identity: tag = its row key, id = its kind. Re-posting updates
 *   it in place; a different reminder never replaces it.
 * - Each kind is a group (events, tasks) with a summary (InboxStyle: one line per reminder
 *   and a count). Children alert, the summary never does (GROUP_ALERT_CHILDREN), so a burst of
 *   reminders sounds once per new reminder and nothing is silently swallowed.
 * - Actions stay on each child: events get Snooze 5 min, Snooze…, Dismiss (Join in its place
 *   when the event has a conference link); tasks get Done, Snooze…, Dismiss. They are broadcasts, so they work from the lock screen and offline.
 */
class ReminderNotifier(private val context: Context) : ReminderSink {
    private val nm = NotificationManagerCompat.from(context)

    private fun allowed() = nm.areNotificationsEnabled() &&
        ContextCompat.checkSelfPermission(context, Manifest.permission.POST_NOTIFICATIONS) == PackageManager.PERMISSION_GRANTED

    private fun id(kind: String) = if (kind == Kind.EVENT) EVENT_ID else TASK_ID
    private fun group(kind: String) = if (kind == Kind.EVENT) GROUP_EVENTS else GROUP_TASKS
    private fun summaryTag(kind: String) = "summary-$kind"

    override fun post(r: ReminderEntity, alert: Boolean) = post(r, alert, null)

    override fun post(r: ReminderEntity, alert: Boolean, join: String?) = post(r, alert, join, AlertStyle())

    /**
     * Whether a full-screen alert may be used now: the user allowed it in system settings (Android
     * 14+ asks per app), and Do Not Disturb is off. Otherwise the reminder is a heads-up as usual.
     */
    fun fullScreenAllowed(): Boolean {
        val sys = context.getSystemService(NotificationManager::class.java)
        val dnd = sys.currentInterruptionFilter.let { it != NotificationManager.INTERRUPTION_FILTER_ALL && it != NotificationManager.INTERRUPTION_FILTER_UNKNOWN }
        return !dnd && nm.canUseFullScreenIntent()
    }

    override fun post(r: ReminderEntity, alert: Boolean, join: String?, style: AlertStyle) {
        if (!allowed()) return
        if (r.kind == Kind.NOTE) return postNote(r, alert, style)
        val event = r.kind == Kind.EVENT
        val channel = when {
            r.missed -> ReminderChannels.MISSED
            event -> ReminderChannels.EVENTS
            else -> ReminderChannels.TASKS
        }
        val now = System.currentTimeMillis()  // clock-ok: notification timestamp
        val text = if (r.missed) AppText.get(R.string.rem_missed_detail, r.detail) else r.detail
        val b = NotificationCompat.Builder(context, channel)
            .setSmallIcon(if (event) R.drawable.ic_stat_event else R.drawable.ic_stat_task)
            .setColor(ContextCompat.getColor(context, R.color.brand))
            .setContentTitle(r.title.ifBlank { if (event) AppText.get(R.string.rem_event_reminder) else AppText.get(R.string.rem_task_reminder) })
            .setContentText(text)
            .setSubText(if (event) startsIn(r, now) else null)
            .setCategory(if (event) NotificationCompat.CATEGORY_EVENT else NotificationCompat.CATEGORY_REMINDER)
            .setPriority(if (r.missed) NotificationCompat.PRIORITY_DEFAULT else NotificationCompat.PRIORITY_HIGH)
            .setGroup(group(r.kind))
            .setGroupAlertBehavior(NotificationCompat.GROUP_ALERT_CHILDREN)
            .setSortKey("%015d".format(r.startAt ?: r.fireAt))
            // A repeat sounds again although the notification is already there.
            .setOnlyAlertOnce(!style.again)
            .setSilent(!alert)
            .setAutoCancel(false) // a tap opens the sheet; only an action ends the reminder
            .setContentIntent(open(r))
            .setDeleteIntent(action(ReminderActionReceiver.DELETED, r.key))
            .setPublicVersion(NotificationCompat.Builder(context, channel)
                .setSmallIcon(if (event) R.drawable.ic_stat_event else R.drawable.ic_stat_task)
                .setContentTitle(if (event) AppText.get(R.string.rem_event_reminder) else AppText.get(R.string.rem_task_reminder))
                .setContentText(if (event) r.detail.substringBefore(" · ") else AppText.get(R.string.rem_due)).build())
        r.startAt?.let { start ->
            b.setWhen(start).setShowWhen(true)
            // A live countdown for events starting within the hour.
            if (event && start > now && start - now <= 60 * 60_000L) b.setUsesChronometer(true).setChronometerCountDown(true)
        }
        // ACT reminder.full_screen @android.notification: an imminent event or a task due now takes the screen, when the
        // user turned it on, the system allows it and Do Not Disturb is off. The
        // notification stays; the alert screen handles nothing by itself.
        if (style.fullScreen && alert && fullScreenAllowed()) b.setFullScreenIntent(fullScreen(r), true)
        // Events clean themselves up half an hour after they end.
        if (event) r.endAt?.let { end -> (end + 30 * 60_000L - now).takeIf { it > 0 }?.let(b::setTimeoutAfter) }
        // ACT event.snooze @android.notification, ACT event.remind_before @android.notification, ACT reminder.dismiss @android.notification,
        // ACT task.complete @android.notification, ACT task.snooze @android.notification
        // ACT agent.ask_about @android.notification: with "Ask on reminders" on (the default), Ask takes the picker
        // button's place; the picker stays one tap away in the sheet.
        if (event) {
            b.addAction(R.drawable.ic_stat_event, AppText.get(R.string.rem_action_snooze_5_min), action(ReminderActionReceiver.SNOOZE, r.key, "5m"))
            val ahead = (r.startAt ?: 0L) - now > 60_000L
            if (!style.ask) {
                if (ahead) b.addAction(R.drawable.ic_stat_event, AppText.get(R.string.rem_action_remind_before), sheet(r, "offset"))
                else b.addAction(R.drawable.ic_stat_event, AppText.get(R.string.rem_action_snooze_more), sheet(r, "snooze"))
            }
            // ACT event.join @android.notification: with a conference link Join takes Dismiss's place; a swipe still dismisses.
            // ACT event.prep @android.notification: without one, an event with guests offers Prep there (its meeting prep).
            if (join != null) b.addAction(R.drawable.ic_stat_event, AppText.get(R.string.rem_action_join), joinIntent(r, join))
            else if (style.prep) b.addAction(R.drawable.ic_stat_event, AppText.get(R.string.rem_action_prep), prepIntent(r))
            else b.addAction(R.drawable.ic_stat_event, AppText.get(R.string.rem_action_dismiss), action(ReminderActionReceiver.DISMISS, r.key))
            if (style.ask) b.addAction(R.drawable.ic_stat_event, AppText.get(R.string.rem_action_ask, io.github.murtazanasir.mailr.i18n.AssistantName.now()), ask(r))
        } else {
            b.addAction(R.drawable.ic_stat_task, AppText.get(R.string.rem_action_done), action(ReminderActionReceiver.DONE, r.key))
            b.addAction(R.drawable.ic_stat_task, AppText.get(R.string.rem_action_snooze_1_h), action(ReminderActionReceiver.SNOOZE, r.key, "1h"))
            if (style.ask) b.addAction(R.drawable.ic_stat_task, AppText.get(R.string.rem_action_ask, io.github.murtazanasir.mailr.i18n.AssistantName.now()), ask(r))
            else b.addAction(R.drawable.ic_stat_task, AppText.get(R.string.rem_action_snooze_more), sheet(r, "snooze"))
        }
        io.github.murtazanasir.mailr.util.Notify.post(context, r.key, id(r.kind), b.build())
    }

    /**
     * A note reminder (keys `n|…`, fired here while the Notes app is not installed): the
     * note's title, a tap opens the note (the reminder stays), Snooze 1 h and Dismiss.
     */
    private fun postNote(r: ReminderEntity, alert: Boolean, style: AlertStyle) {
        val channel = if (r.missed) ReminderChannels.MISSED else ReminderChannels.TASKS
        val open = PendingIntent.getActivity(context, ("note" + r.key).hashCode(),
            Intent(Intent.ACTION_VIEW, Uri.parse("mailr://notes/n/${r.itemUid}"), context, io.github.murtazanasir.mailr.ui.MainActivity::class.java)
                .addFlags(Intent.FLAG_ACTIVITY_NEW_TASK or Intent.FLAG_ACTIVITY_CLEAR_TOP),
            PendingIntent.FLAG_IMMUTABLE or PendingIntent.FLAG_UPDATE_CURRENT)
        val b = NotificationCompat.Builder(context, channel)
            .setSmallIcon(R.drawable.ic_stat_task)
            .setColor(ContextCompat.getColor(context, R.color.brand))
            .setContentTitle(r.title.ifBlank { AppText.get(R.string.rem_note_reminder) })
            .setContentText(if (r.missed) AppText.get(R.string.rem_missed_detail, r.detail) else r.detail)
            .setCategory(NotificationCompat.CATEGORY_REMINDER)
            .setPriority(if (r.missed) NotificationCompat.PRIORITY_DEFAULT else NotificationCompat.PRIORITY_HIGH)
            .setGroup(group(r.kind))
            .setGroupAlertBehavior(NotificationCompat.GROUP_ALERT_CHILDREN)
            .setSortKey("%015d".format(r.fireAt))
            .setOnlyAlertOnce(!style.again)
            .setSilent(!alert)
            .setAutoCancel(false)
            .setContentIntent(open)
            .setDeleteIntent(action(ReminderActionReceiver.DELETED, r.key))
            .setPublicVersion(NotificationCompat.Builder(context, channel).setSmallIcon(R.drawable.ic_stat_task)
                .setContentTitle(AppText.get(R.string.rem_note_reminder)).build())
        // ACT reminder.dismiss @android.notification: a note reminder snoozes or ends like any other (shared reminder_states).
        b.addAction(R.drawable.ic_stat_task, AppText.get(R.string.rem_action_snooze_1_h), action(ReminderActionReceiver.SNOOZE, r.key, "1h"))
        b.addAction(R.drawable.ic_stat_task, AppText.get(R.string.rem_action_dismiss), action(ReminderActionReceiver.DISMISS, r.key))
        io.github.murtazanasir.mailr.util.Notify.post(context, r.key, id(r.kind), b.build())
    }

    private fun startsIn(r: ReminderEntity, now: Long): String? {
        val s = r.startAt ?: return null
        val mins = (s - now) / 60_000
        return when {
            r.detail.startsWith(AppText.get(R.string.rem_all_day)) -> null
            mins > 90 -> null
            mins > 0 -> AppText.plural(R.plurals.rem_starts_in_minutes, mins.toInt(), mins)
            mins > -5 -> AppText.get(R.string.rem_starts_now)
            else -> null
        }
    }

    override fun cancel(r: ReminderEntity) {
        nm.cancel(r.key, id(r.kind))
    }

    override fun postSummary(spec: SummarySpec) {
        if (!allowed()) return
        val event = spec.kind == Kind.EVENT
        val style = NotificationCompat.InboxStyle().setBigContentTitle(spec.title)
        spec.lines.forEach { style.addLine(it) }
        if (spec.more > 0) style.setSummaryText(AppText.plural(R.plurals.rem_more, spec.more, spec.more))
        val n = NotificationCompat.Builder(context, if (event) ReminderChannels.EVENTS else ReminderChannels.TASKS)
            .setSmallIcon(if (event) R.drawable.ic_stat_event else R.drawable.ic_stat_task)
            .setColor(ContextCompat.getColor(context, R.color.brand))
            .setContentTitle(spec.title)
            .setContentText(spec.lines.firstOrNull())
            .setStyle(style)
            .setNumber(spec.count)
            .setGroup(group(spec.kind))
            .setGroupSummary(true)
            .setGroupAlertBehavior(NotificationCompat.GROUP_ALERT_CHILDREN)
            .setSilent(true)
            .setOnlyAlertOnce(true)
            .setAutoCancel(false)
            .setCategory(if (event) NotificationCompat.CATEGORY_EVENT else NotificationCompat.CATEGORY_REMINDER)
            // The summary opens the triage queue over every reminder showing (ACT notification.open_stack @android.notification).
            .setContentIntent(PendingIntent.getActivity(context, if (event) 11 else 12,
                ReminderActivity.intent(context, stack = true), PendingIntent.FLAG_IMMUTABLE or PendingIntent.FLAG_UPDATE_CURRENT))
            .setDeleteIntent(action(ReminderActionReceiver.SUMMARY_DELETED, spec.kind))
            .build()
        io.github.murtazanasir.mailr.util.Notify.post(context, summaryTag(spec.kind), id(spec.kind), n)
    }

    override fun cancelSummary(kind: String) {
        nm.cancel(summaryTag(kind), id(kind))
    }

    override fun activeKeys(): Set<String>? = catching {
        context.getSystemService(NotificationManager::class.java).activeNotifications
            .filter { it.id == EVENT_ID || it.id == TASK_ID }.mapNotNull { it.tag }.filterNot { it.startsWith("summary-") }.toSet()
    }.getOrNull()

    /** A tap: the quick-action sheet for this reminder (ACT notification.open @android.notification). It changes nothing by itself. */
    private fun open(r: ReminderEntity): PendingIntent = PendingIntent.getActivity(context, ("open" + r.key).hashCode(),
        ReminderActivity.intent(context, key = r.key), PendingIntent.FLAG_IMMUTABLE or PendingIntent.FLAG_UPDATE_CURRENT)

    /**
     * Ask: the ask capture with this task or event attached (ACT agent.ask_about). The capture never
     * shows over the lock screen, so the phone asks to be unlocked first. The reminder stays.
     */
    private fun ask(r: ReminderEntity): PendingIntent {
        val about = if (r.kind == Kind.EVENT) io.github.murtazanasir.mailr.data.agent.AskAbout.Event(r.itemUid, r.occurrence, r.title)
            else io.github.murtazanasir.mailr.data.agent.AskAbout.Task(r.itemUid, r.title)
        return PendingIntent.getActivity(context, ("ask" + r.key).hashCode(),
            io.github.murtazanasir.mailr.ui.ask.AskIntents.capture(context, about, from = "notification"),
            PendingIntent.FLAG_IMMUTABLE or PendingIntent.FLAG_UPDATE_CURRENT)
    }

    /** The full-screen alert for [r] (ReminderAlertActivity); it shows over the lock screen. */
    private fun fullScreen(r: ReminderEntity): PendingIntent = PendingIntent.getActivity(context, ("alert" + r.key).hashCode(),
        io.github.murtazanasir.mailr.ui.calendar.ReminderAlertActivity.intent(context, r.key),
        PendingIntent.FLAG_IMMUTABLE or PendingIntent.FLAG_UPDATE_CURRENT)

    /** ACT reminder.agenda @android.notification: today's events and tasks, quietly; a tap opens the calendar's schedule. */
    override fun postAgenda(agenda: Agenda) {
        if (!allowed()) return
        val style = NotificationCompat.InboxStyle().setBigContentTitle(agenda.title)
        agenda.lines.forEach { style.addLine(it) }
        if (agenda.more > 0) style.setSummaryText(AppText.plural(R.plurals.rem_more, agenda.more, agenda.more))
        val n = NotificationCompat.Builder(context, ReminderChannels.AGENDA)
            .setSmallIcon(R.drawable.ic_stat_event)
            .setColor(ContextCompat.getColor(context, R.color.brand))
            .setContentTitle(agenda.title)
            .setContentText(agenda.lines.firstOrNull())
            .setStyle(style)
            .setCategory(NotificationCompat.CATEGORY_EVENT)
            .setPriority(NotificationCompat.PRIORITY_LOW)
            .setAutoCancel(true)
            .setContentIntent(PendingIntent.getActivity(context, 13,
                Intent(context, io.github.murtazanasir.mailr.ui.MainActivity::class.java).setAction(Intent.ACTION_VIEW)
                    .setData(Uri.parse("mailr://calendar/schedule")).addFlags(Intent.FLAG_ACTIVITY_NEW_TASK),
                PendingIntent.FLAG_IMMUTABLE or PendingIntent.FLAG_UPDATE_CURRENT))
            .setPublicVersion(NotificationCompat.Builder(context, ReminderChannels.AGENDA)
                .setSmallIcon(R.drawable.ic_stat_event).setContentTitle(AppText.get(R.string.rem_agenda_public_title))
                .setContentText(listOfNotNull(agenda.events.takeIf { it > 0 }?.let { AppText.plural(R.plurals.rem_agenda_events, it, it) },
                    agenda.tasks.takeIf { it > 0 }?.let { AppText.plural(R.plurals.rem_agenda_tasks, it, it) }).joinToString(", ")).build())
            .build()
        io.github.murtazanasir.mailr.util.Notify.post(context, "agenda", AGENDA_ID, n)
    }

    /** Join: the meeting link in a browser or the meeting app; the reminder stays until it is handled. */
    private fun joinIntent(r: ReminderEntity, url: String): PendingIntent = PendingIntent.getActivity(context, ("join" + r.key).hashCode(),
        Intent(Intent.ACTION_VIEW, Uri.parse(url)).addCategory(Intent.CATEGORY_BROWSABLE).addFlags(Intent.FLAG_ACTIVITY_NEW_TASK),
        PendingIntent.FLAG_IMMUTABLE or PendingIntent.FLAG_UPDATE_CURRENT)

    /** Prep: the app on the event with its meeting prep open. It handles nothing; the reminder stays. */
    private fun prepIntent(r: ReminderEntity): PendingIntent = PendingIntent.getActivity(context, ("prep" + r.key).hashCode(),
        Intent(context, io.github.murtazanasir.mailr.ui.MainActivity::class.java).setAction(Intent.ACTION_VIEW)
            .setData(io.github.murtazanasir.mailr.ui.calendar.prepUri(r.itemUid, r.occurrence))
            .addFlags(Intent.FLAG_ACTIVITY_NEW_TASK or Intent.FLAG_ACTIVITY_CLEAR_TOP),
        PendingIntent.FLAG_IMMUTABLE or PendingIntent.FLAG_UPDATE_CURRENT)

    /** "Snooze…" / "Remind before…": the sheet, opened on that picker. */
    private fun sheet(r: ReminderEntity, expand: String): PendingIntent = PendingIntent.getActivity(context, (expand + r.key).hashCode(),
        ReminderActivity.intent(context, key = r.key, expand = expand), PendingIntent.FLAG_IMMUTABLE or PendingIntent.FLAG_UPDATE_CURRENT)

    private fun action(what: String, key: String, extra: String? = null): PendingIntent =
        PendingIntent.getBroadcast(context, (what + key + extra).hashCode(),
            Intent(context, ReminderActionReceiver::class.java).setAction(what).putExtra("key", key).putExtra("choice", extra),
            PendingIntent.FLAG_IMMUTABLE or PendingIntent.FLAG_UPDATE_CURRENT)

    companion object {
        const val EVENT_ID = 5101
        const val TASK_ID = 5102
        const val AGENDA_ID = 5103
        const val GROUP_EVENTS = "io.github.murtazanasir.mailr.reminders.events"
        const val GROUP_TASKS = "io.github.murtazanasir.mailr.reminders.tasks"
    }
}

/** Runs [block] off the main thread and keeps the receiver alive until it finishes (≤ 9 s). */
internal fun BroadcastReceiver.async(block: suspend () -> Unit) {
    val pending = goAsync()
    receiverScope.launch {
        try {
            withTimeoutOrNull(9_000) { block() }
        } finally {
            pending.finish()
        }
    }
}

private val receiverScope = CoroutineScope(SupervisorJob() + Dispatchers.IO)

private val Context.graph get() = (applicationContext as MailrApp).graph

class ReminderAlarmReceiver : BroadcastReceiver() {
    override fun onReceive(context: Context, intent: Intent) {
        if (intent.action != ACTION_FIRE) return
        async { context.graph.reminders.fire() }
    }

    companion object {
        const val ACTION_FIRE = "io.github.murtazanasir.mailr.REMINDER_FIRE"
    }
}

/** Boot, app update, clock and time-zone changes, exact-alarm permission changes: re-plan. */
class ReminderSystemReceiver : BroadcastReceiver() {
    override fun onReceive(context: Context, intent: Intent) {
        val g = context.graph
        when (intent.action) {
            Intent.ACTION_BOOT_COMPLETED, Intent.ACTION_MY_PACKAGE_REPLACED -> async {
                g.reminders.onBoot()
                g.startCalendarWork()
            }
            Intent.ACTION_TIME_CHANGED, Intent.ACTION_TIMEZONE_CHANGED,
            "android.app.action.SCHEDULE_EXACT_ALARM_PERMISSION_STATE_CHANGED" -> async { g.reminders.resync() }
        }
    }
}

class ReminderActionReceiver : BroadcastReceiver() {
    override fun onReceive(context: Context, intent: Intent) {
        val key = intent.getStringExtra("key") ?: return
        val g = context.graph
        async {
            when (intent.action) {
                SNOOZE -> g.reminders.snooze(key, intent.getStringExtra("choice") ?: "5m", g.settings.prefs.first().morningHour)
                DONE -> g.reminders.done(key)
                DISMISS, DELETED -> g.reminders.dismiss(key)
                SUMMARY_DELETED -> g.reminders.dismissAll(key)
            }
        }
    }

    companion object {
        const val SNOOZE = "io.github.murtazanasir.mailr.REMINDER_SNOOZE"
        const val DONE = "io.github.murtazanasir.mailr.REMINDER_DONE"
        const val DISMISS = "io.github.murtazanasir.mailr.REMINDER_DISMISS"
        const val DELETED = "io.github.murtazanasir.mailr.REMINDER_DELETED"
        const val SUMMARY_DELETED = "io.github.murtazanasir.mailr.REMINDER_SUMMARY_DELETED"
    }
}
