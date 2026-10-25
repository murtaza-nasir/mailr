package io.github.murtazanasir.mailr.data.push

import io.github.murtazanasir.mailr.util.catching

import android.app.Notification
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.app.Service
import android.content.Context
import android.content.Intent
import android.content.pm.ServiceInfo
import android.os.Build
import android.os.IBinder
import android.util.Log
import androidx.core.app.NotificationCompat
import androidx.core.app.ServiceCompat
import androidx.work.Constraints
import androidx.work.ExistingWorkPolicy
import androidx.work.NetworkType
import androidx.work.OneTimeWorkRequestBuilder
import androidx.work.OutOfQuotaPolicy
import androidx.work.WorkManager
import io.github.murtazanasir.mailr.MailrApp
import io.github.murtazanasir.mailr.R
import io.github.murtazanasir.mailr.data.api.MailrJson
import io.github.murtazanasir.mailr.data.sync.SyncWorker
import io.github.murtazanasir.mailr.ui.MainActivity
import kotlinx.coroutines.CompletableDeferred
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.Job
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.cancel
import kotlinx.coroutines.delay
import kotlinx.coroutines.isActive
import kotlinx.coroutines.launch
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.contentOrNull
import kotlinx.serialization.json.jsonPrimitive
import okhttp3.Request
import okhttp3.Response
import okhttp3.sse.EventSource
import okhttp3.sse.EventSourceListener
import okhttp3.sse.EventSources
import java.util.concurrent.TimeUnit
import kotlin.math.min

/**
 * Settings → Notifications → Live connection: for a phone
 * with no push app (no UnifiedPush distributor, or one that does not work), a foreground service
 * keeps the server's event stream (`/events`) open, and each event about new mail, an approval, a
 * failed send or an Ambrose result starts the same check the periodic poll runs ([SyncWorker]),
 * so mail arrives at once with no other app installed.
 *
 * The cost, said plainly in the app: Android shows a permanent notification for the service, and
 * the phone keeps one more connection awake. A push app such as ntfy shares one connection among
 * every app on the phone, so it uses less battery. Off by default; it stands aside while push
 * through a push app works.
 */
object LiveConnection {
    const val CHANNEL = "connection"
    private const val NOTIFICATION_ID = 7301

    /** Whether the service should run now. */
    fun wanted(on: Boolean, signedIn: Boolean, push: PushInfo): Boolean = on && signedIn && !push.healthy

    /** A server event that means "look for new mail, approvals and results now". */
    fun relevant(type: String?, entity: String?): Boolean = when (type) {
        // approval.decided: the check reads the approvals and takes down those no longer waiting.
        "notification", "outbox", "approval.decided" -> true
        "changes" -> entity == null || entity == "conversation" || entity == "approval" || entity == "assistant"
        else -> false
    }

    /** Start or stop the service to match [wanted]. A start Android refuses (from the background) is retried at the next app start. */
    fun apply(context: Context, run: Boolean) {
        val intent = Intent(context, LiveConnectionService::class.java)
        if (!run) {
            context.stopService(intent)
            return
        }
        catching {
            if (Build.VERSION.SDK_INT >= 26) context.startForegroundService(intent) else context.startService(intent)
        }.onFailure { Log.i("mailr", "live connection not started now: ${it.message}") }
    }

    /** One catch-up now (new mail, approvals, Ambrose), as a push message would start. */
    fun checkNow(context: Context) {
        WorkManager.getInstance(context).enqueueUniqueWork("live-check", ExistingWorkPolicy.KEEP,
            OneTimeWorkRequestBuilder<SyncWorker>()
                .setConstraints(Constraints.Builder().setRequiredNetworkType(NetworkType.CONNECTED).build())
                .setExpedited(OutOfQuotaPolicy.RUN_AS_NON_EXPEDITED_WORK_REQUEST).build())
    }

    fun createChannel(context: Context) {
        val nm = context.getSystemService(NotificationManager::class.java)
        nm.createNotificationChannel(NotificationChannel(CHANNEL, context.getString(R.string.channel_connection),
            NotificationManager.IMPORTANCE_MIN).apply { description = context.getString(R.string.channel_connection_desc) })
    }

    internal fun notification(context: Context): Notification {
        val open = PendingIntent.getActivity(context, NOTIFICATION_ID,
            Intent(context, MainActivity::class.java).setFlags(Intent.FLAG_ACTIVITY_NEW_TASK or Intent.FLAG_ACTIVITY_SINGLE_TOP),
            PendingIntent.FLAG_IMMUTABLE or PendingIntent.FLAG_UPDATE_CURRENT)
        return NotificationCompat.Builder(context, CHANNEL)
            .setSmallIcon(R.drawable.ic_launcher_monochrome)
            .setContentTitle(context.getString(R.string.push_live_title))
            .setContentText(context.getString(R.string.push_live_text))
            .setContentIntent(open)
            .setOngoing(true)
            .setPriority(NotificationCompat.PRIORITY_MIN)
            .setCategory(NotificationCompat.CATEGORY_SERVICE)
            .build()
    }

    internal const val ID = NOTIFICATION_ID
}

/** The foreground service behind [LiveConnection] (manifest: foregroundServiceType specialUse). */
class LiveConnectionService : Service() {
    private val scope = CoroutineScope(SupervisorJob() + Dispatchers.IO)
    private var job: Job? = null
    private var source: EventSource? = null
    private var checkJob: Job? = null

    override fun onBind(intent: Intent?): IBinder? = null

    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int {
        LiveConnection.createChannel(this)
        ServiceCompat.startForeground(this, LiveConnection.ID, LiveConnection.notification(this),
            if (Build.VERSION.SDK_INT >= 34) ServiceInfo.FOREGROUND_SERVICE_TYPE_SPECIAL_USE else 0)
        if (job?.isActive != true) job = scope.launch { loop() }
        return START_STICKY
    }

    override fun onDestroy() {
        source?.cancel()
        scope.cancel()
        super.onDestroy()
    }

    private suspend fun loop() {
        val g = (application as MailrApp).graph
        var backoff = 2_000L
        while (scope.isActive) {
            if (g.sessions.session.value == null) {
                stopSelf()
                return
            }
            val opened = connectOnce(g)
            backoff = if (opened) 2_000L else min(backoff * 2, 5 * 60_000L)
            delay(backoff)
        }
    }

    private suspend fun connectOnce(g: io.github.murtazanasir.mailr.AppGraph): Boolean {
        val done = CompletableDeferred<Boolean>()
        val http = g.client.http.newBuilder().readTimeout(90, TimeUnit.SECONDS).build()
        val req = Request.Builder().url(g.client.url("events")).header("Accept", "text/event-stream").build()
        var opened = false
        source = EventSources.createFactory(http).newEventSource(req, object : EventSourceListener() {
            override fun onOpen(eventSource: EventSource, response: Response) {
                opened = true
                soon() // what arrived while the connection was down
            }

            override fun onEvent(eventSource: EventSource, id: String?, type: String?, data: String) {
                val obj = catching { MailrJson.parseToJsonElement(data) as? JsonObject }.getOrNull() ?: return
                val t = obj["type"]?.jsonPrimitive?.contentOrNull ?: type
                if (LiveConnection.relevant(t, obj["entity"]?.jsonPrimitive?.contentOrNull)) soon()
            }

            override fun onClosed(eventSource: EventSource) {
                done.complete(opened)
            }

            override fun onFailure(eventSource: EventSource, t: Throwable?, response: Response?) {
                done.complete(opened)
            }
        })
        return try { done.await() } finally { source?.cancel() }
    }

    /** Several events in a row (a burst of mail) make one check. */
    private fun soon() {
        if (checkJob?.isActive == true) return
        checkJob = scope.launch {
            delay(1_500)
            LiveConnection.checkNow(this@LiveConnectionService)
        }
    }
}
