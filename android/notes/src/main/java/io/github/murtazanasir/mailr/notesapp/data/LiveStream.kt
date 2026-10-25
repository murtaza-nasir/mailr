package io.github.murtazanasir.mailr.notesapp.data

import io.github.murtazanasir.mailr.util.catching

import android.util.Log
import io.github.murtazanasir.mailr.data.api.ApiClient
import io.github.murtazanasir.mailr.data.api.MailrJson
import kotlinx.coroutines.CompletableDeferred
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Job
import kotlinx.coroutines.delay
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
 * The server's change stream (`/events`) while the Notes app is in the foreground: note and
 * table events go to the repository ([onEvent]), a reconnect reads what changed meanwhile ("open"),
 * and a reminder change ([onReminders]) plans the note reminders again. In the background the
 * periodic sync takes over.
 */
class LiveStream(
    private val client: ApiClient,
    private val scope: CoroutineScope,
    private val onEvent: (String, JsonObject) -> Unit,
    private val onReminders: () -> Unit = {},
) {
    private var source: EventSource? = null
    private var job: Job? = null

    fun start() {
        if (job?.isActive == true) return
        job = scope.launch {
            var backoff = 1_000L
            while (true) {
                val opened = connectOnce()
                backoff = if (opened) 1_000L else min(backoff * 2, 60_000L)
                delay(backoff)
            }
        }
    }

    fun stop() {
        job?.cancel()
        job = null
        source?.cancel()
        source = null
    }

    private suspend fun connectOnce(): Boolean {
        val done = CompletableDeferred<Boolean>()
        val http = client.http.newBuilder().readTimeout(90, TimeUnit.SECONDS).build()
        val req = catching { Request.Builder().url(client.url("events")).header("Accept", "text/event-stream").build() }
            .getOrElse { return false }
        var opened = false
        source = EventSources.createFactory(http).newEventSource(req, object : EventSourceListener() {
            override fun onOpen(eventSource: EventSource, response: Response) {
                opened = true
                onEvent("open", JsonObject(emptyMap()))
                onReminders()
            }

            override fun onEvent(eventSource: EventSource, id: String?, type: String?, data: String) {
                val obj = catching { MailrJson.parseToJsonElement(data) as? JsonObject }.getOrNull() ?: return
                when (val t = obj["type"]?.jsonPrimitive?.contentOrNull ?: type ?: return) {
                    "changes" -> if (obj["entity"]?.jsonPrimitive?.contentOrNull == "reminder") onReminders()
                    "ping", "hello" -> Unit
                    else -> onEvent(t, obj)
                }
            }

            override fun onClosed(eventSource: EventSource) {
                done.complete(opened)
            }

            override fun onFailure(eventSource: EventSource, t: Throwable?, response: Response?) {
                if (t != null) Log.d("notes", "event stream: ${t.message}")
                done.complete(opened)
            }
        })
        return try {
            done.await()
        } finally {
            source?.cancel()
        }
    }
}
