package io.github.murtazanasir.mailr.notesapp.reminders

import io.github.murtazanasir.mailr.util.catching

import io.github.murtazanasir.mailr.time.AppClock
import io.github.murtazanasir.mailr.i18n.AppText
import android.app.AlarmManager
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent
import android.net.Uri
import android.os.Build
import androidx.core.app.NotificationCompat
import androidx.core.app.NotificationManagerCompat
import androidx.work.BackoffPolicy
import androidx.work.Constraints
import androidx.work.CoroutineWorker
import androidx.work.NetworkType
import androidx.work.OneTimeWorkRequestBuilder
import androidx.work.WorkManager
import androidx.work.WorkerParameters
import androidx.work.workDataOf
import io.github.murtazanasir.mailr.data.api.ApiClient
import io.github.murtazanasir.mailr.data.api.MailrJson
import io.github.murtazanasir.mailr.notes.data.NotesRepository
import io.github.murtazanasir.mailr.notesapp.DeepLinks
import io.github.murtazanasir.mailr.notesapp.MainActivity
import io.github.murtazanasir.mailr.notesapp.NotesApplication
import io.github.murtazanasir.mailr.notesapp.R
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.launch
import kotlinx.coroutines.sync.Mutex
import kotlinx.coroutines.sync.withLock
import kotlinx.coroutines.withContext
import kotlinx.serialization.Serializable
import kotlinx.serialization.SerialName
import kotlinx.serialization.json.JsonArray
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.JsonPrimitive
import java.time.Instant
import java.time.OffsetDateTime
import java.util.concurrent.TimeUnit

/** One note reminder from the phone's copy of the notes (`n|<note>|abs|<instant>`). */
data class NoteReminderSource(val key: String, val noteId: String, val title: String, val list: Boolean, val at: Long)

/** A reminder's shared state from `GET /reminders/state`: handled, snoozed until, or pending. */
@Serializable
data class RemoteState(val key: String, val state: String, @SerialName("snoozed_until") val snoozedUntil: String? = null)

@Serializable
private data class StatesPage(val items: List<RemoteState> = emptyList(), val next: String? = null)

/**
 * What to do now: the reminders due ([due]) and the next alarm ([next], or null).
 * Pure, so the rules are tested without alarms or notifications.
 */
data class ReminderPlan(val due: List<NoteReminderSource>, val next: Long?)

object NotePlanner {
    /** A reminder more than this late is left quiet (a phone off for a week does not ring old notes). */
    const val TOO_LATE_MS = 24 * 3_600_000L

    /**
     * [shown]: the time each key last rang here. A handled key never rings; a snoozed key rings at
     * its snooze time; a key rings once per time it is due.
     */
    fun plan(reminders: List<NoteReminderSource>, remote: Map<String, RemoteState>, shown: Map<String, Long>, now: Long): ReminderPlan {
        val due = ArrayList<NoteReminderSource>()
        var next: Long? = null
        for (r in reminders) {
            val st = remote[r.key]
            if (st?.state == "handled") continue
            val at = if (st?.state == "snoozed") parse(st.snoozedUntil) ?: r.at else r.at
            if (shown[r.key] == at) continue
            if (at <= now) {
                if (now - at <= TOO_LATE_MS) due += r.copy(at = at)
            } else if (next == null || at < next) next = at
        }
        return ReminderPlan(due, next)
    }

    fun parse(iso: String?): Long? = iso?.let {
        catching { OffsetDateTime.parse(it).toInstant().toEpochMilli() }.getOrNull()
            ?: catching { Instant.parse(it).toEpochMilli() }.getOrNull()
    }
}

/**
 * Note reminders on the phone (they ring
 * from the Notes app once it is installed, and the mailr app then leaves `n|` keys alone). The plan
 * comes from the phone's copy of the notes ([NotesRepository.noteReminders]) and the shared states
 * (`GET /reminders/state?kinds=n`); Snooze 1 h and Dismiss go back to the server, so every device and
 * the web agree. ACT note.remind @android.notesapp
 */
class NoteReminders(
    private val context: Context,
    private val repo: NotesRepository,
    private val client: ApiClient,
    /** Off in tests: alarms are read back from [nextAlarm] instead. */
    private val armAlarms: Boolean = true,
    private val clock: () -> Long = AppClock::millis,
) {
    private val prefs = context.getSharedPreferences("note_reminders", Context.MODE_PRIVATE)
    private val lock = Mutex()

    /** The next alarm time planned (tests read it). */
    @Volatile var nextAlarm: Long? = null
        private set

    private fun remote(): Map<String, RemoteState> = prefs.all.filterKeys { it.startsWith("s:") }.mapNotNull { (k, v) ->
        val key = k.removePrefix("s:")
        val (state, until) = (v as? String)?.split('\t', limit = 2)?.let { it[0] to it.getOrNull(1) } ?: return@mapNotNull null
        key to RemoteState(key, state, until?.takeIf { it.isNotEmpty() })
    }.toMap()

    private fun shown(): Map<String, Long> = prefs.all.filterKeys { it.startsWith("f:") }
        .mapNotNull { (k, v) -> (v as? Long)?.let { k.removePrefix("f:") to it } }.toMap()

    private fun putRemote(s: RemoteState) {
        prefs.edit().putString("s:" + s.key, s.state + "\t" + (s.snoozedUntil ?: "")).apply()
    }

    suspend fun sources(): List<NoteReminderSource> = repo.noteReminders().mapNotNull { (n, r) ->
        val at = NotePlanner.parse(r.at) ?: return@mapNotNull null
        NoteReminderSource(r.key, n.id, n.title.ifBlank { AppText.get(if (n.isList) io.github.murtazanasir.mailr.notes.R.string.notes_untitled_list else io.github.murtazanasir.mailr.notes.R.string.notes_untitled_note) }, n.isList, at)
    }

    /** ACT reminder.sync: the shared states changed since the cursor (note keys only), then plan. */
    suspend fun sync() {
        lock.withLock {
            var cursor = prefs.getString("cursor", null)
            repeat(10) {
                val q = buildMap { put("kinds", "n"); cursor?.let { put("since", it) } }
                val page = client.get("reminders/state", StatesPage.serializer(), q)
                page.items.forEach { putRemote(it) }
                val next = page.next ?: cursor
                if (next != null) prefs.edit().putString("cursor", next).apply()
                cursor = next
                if (page.items.size < 500) return@withLock
            }
        }
        plan()
    }

    /** Ring what is due, arm the alarm for the next one. */
    suspend fun plan() = lock.withLock {
        val p = NotePlanner.plan(sources(), remote(), shown(), clock())
        val e = prefs.edit()
        p.due.forEach { r ->
            Notifier.post(context, r)
            e.putLong("f:" + r.key, r.at)
        }
        e.apply()
        nextAlarm = p.next
        if (armAlarms) Alarm.arm(context, p.next)
        p
    }

    /** Snooze 1 h or Dismiss from the notification: here at once, on the server through WorkManager. */
    suspend fun act(key: String, action: String) {
        NotificationManagerCompat.from(context).cancel(key, NOTIFICATION_ID)
        when (action) {
            ACTION_SNOOZE -> {
                val until = Instant.ofEpochMilli(clock() + 3_600_000L).toString()
                putRemote(RemoteState(key, "snoozed", until))
                ReminderSendWorker.enqueue(context, "snooze", key, until)
            }
            ACTION_DISMISS -> {
                putRemote(RemoteState(key, "handled"))
                ReminderSendWorker.enqueue(context, "handle", key, null)
            }
        }
        plan()
    }

    /** Sign-out or another account: the states, the rung marks and the alarm go. */
    fun clear() {
        prefs.edit().clear().apply()
        nextAlarm = null
        if (armAlarms) Alarm.arm(context, null)
        catching { NotificationManagerCompat.from(context).cancelAll() }
    }

    companion object {
        const val CHANNEL = "note_reminders"
        const val NOTIFICATION_ID = 7
        const val ACTION_FIRE = "io.github.murtazanasir.mailr.notes.REMINDER_FIRE"
        const val ACTION_SNOOZE = "io.github.murtazanasir.mailr.notes.REMINDER_SNOOZE"
        const val ACTION_DISMISS = "io.github.murtazanasir.mailr.notes.REMINDER_DISMISS"

        fun createChannel(context: Context) {
            val nm = context.getSystemService(NotificationManager::class.java) ?: return
            nm.createNotificationChannel(NotificationChannel(CHANNEL, context.getString(R.string.channel_note_reminders),
                NotificationManager.IMPORTANCE_HIGH).apply { description = context.getString(R.string.channel_note_reminders_description) })
        }
    }
}

/** The reminder's notification: the note's title, a tap opens the note, Snooze 1 h and Dismiss. */
internal object Notifier {
    fun post(context: Context, r: NoteReminderSource) {
        val open = PendingIntent.getActivity(context, ("open" + r.key).hashCode(),
            Intent(Intent.ACTION_VIEW, Uri.parse(DeepLinks.noteUri(r.noteId)), context, MainActivity::class.java)
                .addFlags(Intent.FLAG_ACTIVITY_NEW_TASK or Intent.FLAG_ACTIVITY_CLEAR_TOP),
            PendingIntent.FLAG_IMMUTABLE or PendingIntent.FLAG_UPDATE_CURRENT)
        fun action(a: String) = PendingIntent.getBroadcast(context, (a + r.key).hashCode(),
            Intent(context, NoteAlarmReceiver::class.java).setAction(a).putExtra("key", r.key),
            PendingIntent.FLAG_IMMUTABLE or PendingIntent.FLAG_UPDATE_CURRENT)
        val n = NotificationCompat.Builder(context, NoteReminders.CHANNEL)
            .setSmallIcon(R.drawable.ic_launcher_monochrome)
            .setColor(0xFFFBBC04.toInt())
            .setContentTitle(r.title)
            .setContentText(AppText.get(if (r.list) R.string.notesapp_list_reminder else R.string.notesapp_note_reminder))
            .setCategory(NotificationCompat.CATEGORY_REMINDER)
            .setPriority(NotificationCompat.PRIORITY_HIGH)
            .setAutoCancel(false)
            .setContentIntent(open)
            .setPublicVersion(NotificationCompat.Builder(context, NoteReminders.CHANNEL).setSmallIcon(R.drawable.ic_launcher_monochrome)
                .setContentTitle(AppText.get(R.string.notesapp_note_reminder)).build())
            // ACT reminder.dismiss: a note reminder snoozes or ends like any other (shared reminder_states).
            .addAction(R.drawable.ic_stat_note, AppText.get(R.string.notesapp_snooze_1h), action(NoteReminders.ACTION_SNOOZE))
            .addAction(R.drawable.ic_stat_note, AppText.get(R.string.notesapp_dismiss), action(NoteReminders.ACTION_DISMISS))
            .build()
        // Notifications turned off for the app: the reminder is not shown (the alarm already rang).
        io.github.murtazanasir.mailr.util.Notify.post(context, r.key, NoteReminders.NOTIFICATION_ID, n)
    }
}

/** One alarm for the next reminder; exact when the phone allows it. */
internal object Alarm {
    fun arm(context: Context, at: Long?) {
        val am = context.getSystemService(AlarmManager::class.java) ?: return
        val pi = PendingIntent.getBroadcast(context, 0, Intent(context, NoteAlarmReceiver::class.java).setAction(NoteReminders.ACTION_FIRE),
            PendingIntent.FLAG_IMMUTABLE or PendingIntent.FLAG_UPDATE_CURRENT)
        if (at == null) {
            am.cancel(pi)
            return
        }
        val t = maxOf(at, System.currentTimeMillis() + 1_000)  // clock-ok: AlarmManager runs on the real clock
        val exact = Build.VERSION.SDK_INT < Build.VERSION_CODES.S || am.canScheduleExactAlarms()
        try {
            if (exact) am.setExactAndAllowWhileIdle(AlarmManager.RTC_WAKEUP, t, pi)
            else am.setAndAllowWhileIdle(AlarmManager.RTC_WAKEUP, t, pi)
        } catch (e: SecurityException) {
            am.setAndAllowWhileIdle(AlarmManager.RTC_WAKEUP, t, pi)
        }
    }
}

/** The alarm, and the notification's Snooze 1 h and Dismiss. */
class NoteAlarmReceiver : BroadcastReceiver() {
    override fun onReceive(context: Context, intent: Intent) {
        val g = (context.applicationContext as? NotesApplication)?.graph ?: return
        val pending = goAsync()
        g.scope.launch {
            try {
                when (intent.action) {
                    NoteReminders.ACTION_FIRE -> g.reminders.plan()
                    NoteReminders.ACTION_SNOOZE, NoteReminders.ACTION_DISMISS ->
                        intent.getStringExtra("key")?.let { g.reminders.act(it, intent.action!!) }
                }
            } finally {
                pending.finish()
            }
        }
    }
}

/** After a reboot or an update the alarm is gone: plan again. */
class NoteBootReceiver : BroadcastReceiver() {
    override fun onReceive(context: Context, intent: Intent) {
        val g = (context.applicationContext as? NotesApplication)?.graph ?: return
        val pending = goAsync()
        g.scope.launch { try { catching { g.reminders.plan() } } finally { pending.finish() } }
    }
}

/** A Snooze or Dismiss made on the phone, sent once there is a network (`POST /reminders/snooze|handle`). */
class ReminderSendWorker(ctx: Context, params: WorkerParameters) : CoroutineWorker(ctx, params) {
    override suspend fun doWork(): Result {
        val g = (applicationContext as? NotesApplication)?.graph ?: return Result.success()
        if (g.sessions.session.value == null) return Result.success()
        val key = inputData.getString("key") ?: return Result.success()
        val body = when (inputData.getString("what")) {
            "snooze" -> JsonObject(mapOf("key" to JsonPrimitive(key), "until" to JsonPrimitive(inputData.getString("until") ?: return Result.success())))
            else -> JsonObject(mapOf("keys" to JsonArray(listOf(JsonPrimitive(key)))))
        }
        val path = if (inputData.getString("what") == "snooze") "reminders/snooze" else "reminders/handle"
        return try {
            withContext(Dispatchers.IO) {
                g.client.send("POST", path, JsonObject.serializer(), MailrJson.encodeToString(JsonObject.serializer(), body),
                    idempotencyKey = inputData.getString("idem"))
            }
            Result.success()
        } catch (e: io.github.murtazanasir.mailr.data.api.ApiException) {
            if (e.retryable) Result.retry() else Result.success()
        } catch (e: java.io.IOException) {
            Result.retry()
        }
    }

    companion object {
        fun enqueue(context: Context, what: String, key: String, until: String?) {
            WorkManager.getInstance(context).enqueue(OneTimeWorkRequestBuilder<ReminderSendWorker>()
                .setInputData(workDataOf("what" to what, "key" to key, "until" to until, "idem" to java.util.UUID.randomUUID().toString()))
                .setConstraints(Constraints.Builder().setRequiredNetworkType(NetworkType.CONNECTED).build())
                .setBackoffCriteria(BackoffPolicy.EXPONENTIAL, 30, TimeUnit.SECONDS).build())
        }
    }
}
