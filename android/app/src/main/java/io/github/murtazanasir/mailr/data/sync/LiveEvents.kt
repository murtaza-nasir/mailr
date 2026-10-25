package io.github.murtazanasir.mailr.data.sync

import io.github.murtazanasir.mailr.util.catching

import android.util.Log
import io.github.murtazanasir.mailr.data.api.ApiClient
import io.github.murtazanasir.mailr.data.api.MailrJson
import io.github.murtazanasir.mailr.data.repo.MailRepository
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Job
import kotlinx.coroutines.delay
import kotlinx.coroutines.flow.MutableSharedFlow
import kotlinx.coroutines.flow.SharedFlow
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

/** A server event the UI may care about (approvals, send failures, notifications). */
data class ServerEvent(val type: String, val data: JsonObject)

/**
 * Live updates while the app is in the foreground: SSE `/events`. Change notices are
 * coalesced (250 ms) into list invalidations; after a reconnect the change feed catches up.
 * In the background, [SyncWorker] takes over.
 */
class LiveEvents(private val client: ApiClient, private val repo: MailRepository, private val scope: CoroutineScope) {
    /** Entities that changed on the server, for the parts outside mail (calendar, reminders). */
    var onEntities: ((Set<String>) -> Unit)? = null

    private var source: EventSource? = null
    private var job: Job? = null
    private val pending = mutableSetOf<String>()
    private var flushJob: Job? = null

    private val _events = MutableSharedFlow<ServerEvent>(extraBufferCapacity = 16)
    val events: SharedFlow<ServerEvent> = _events

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
        val done = kotlinx.coroutines.CompletableDeferred<Boolean>()
        val http = client.http.newBuilder().readTimeout(90, TimeUnit.SECONDS).build()
        val req = Request.Builder().url(client.url("events")).header("Accept", "text/event-stream").build()
        var opened = false
        source = EventSources.createFactory(http).newEventSource(req, object : EventSourceListener() {
            override fun onOpen(eventSource: EventSource, response: Response) {
                opened = true
                // Anything that changed while disconnected shows up via a refresh.
                queue(setOf("conversation", "label"))
                // For the parts that keep their own lists (approvals): read them again.
                _events.tryEmit(ServerEvent("open", JsonObject(emptyMap())))
            }

            override fun onEvent(eventSource: EventSource, id: String?, type: String?, data: String) {
                val obj = catching { MailrJson.parseToJsonElement(data) as? JsonObject }.getOrNull() ?: return
                val t = obj["type"]?.jsonPrimitive?.contentOrNull ?: type ?: return
                when (t) {
                    "changes" -> queue(setOf(obj["entity"]?.jsonPrimitive?.contentOrNull ?: "conversation"))
                    "ping", "hello" -> Unit
                    else -> _events.tryEmit(ServerEvent(t, obj))
                }
            }

            override fun onClosed(eventSource: EventSource) {
                done.complete(opened)
            }

            override fun onFailure(eventSource: EventSource, t: Throwable?, response: Response?) {
                if (t != null) Log.d("mailr", "event stream: ${t.message}")
                done.complete(opened)
            }
        })
        return try {
            done.await()
        } finally {
            source?.cancel()
        }
    }

    private fun queue(entities: Set<String>) {
        synchronized(pending) { pending += entities }
        if (flushJob?.isActive == true) return
        flushJob = scope.launch {
            delay(250)
            val batch = synchronized(pending) { pending.toSet().also { pending.clear() } }
            if (batch.isNotEmpty()) {
                onEntities?.invoke(batch)
                repo.invalidate(batch)
                if ("label" in batch || "conversation" in batch) catching { repo.refreshLabels() }
            }
        }
    }
}
