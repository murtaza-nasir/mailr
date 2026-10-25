package io.github.murtazanasir.mailr

import io.github.murtazanasir.mailr.data.api.Approval
import io.github.murtazanasir.mailr.data.api.ConversationSummary
import io.github.murtazanasir.mailr.data.api.MailrJson
import io.github.murtazanasir.mailr.data.api.Outbox
import kotlinx.serialization.builtins.ListSerializer
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.jsonObject
import okhttp3.mockwebserver.Dispatcher
import okhttp3.mockwebserver.MockResponse
import okhttp3.mockwebserver.MockWebServer
import okhttp3.mockwebserver.RecordedRequest
import java.util.concurrent.CopyOnWriteArrayList
import java.util.concurrent.atomic.AtomicLong

/**
 * mailr's push routes and the reads a push message triggers, on a MockWebServer:
 * GET/POST/DELETE /push/targets, POST /push/targets/{id}/test, GET /conversations, GET /approvals,
 * GET /outbox. [down] makes every read fail (503), as when the server cannot be reached.
 */
class FakePushServer {
    data class Call(val method: String, val path: String, val body: String) {
        val json: JsonObject get() = MailrJson.parseToJsonElement(body).jsonObject
    }

    val server = MockWebServer()
    val calls = CopyOnWriteArrayList<Call>()
    private val nextId = AtomicLong(100)
    /** endpoint → target id, as the server's upsert by endpoint. */
    val targets = java.util.concurrent.ConcurrentHashMap<String, Long>()

    @Volatile var conversations: List<ConversationSummary> = emptyList()
    @Volatile var approvals: List<Approval> = emptyList()
    @Volatile var failed: List<Outbox> = emptyList()
    @Volatile var down = false
    @Volatile var testAnswer = """{"ok":true,"error":null,"removed":false}"""
    /** mailr's VAPID public key in GET /push/targets (null: a server from before the key). */
    @Volatile var vapidKey: String? = null
    /** False: GET /push/targets answers 404, as a server or proxy without the route. */
    @Volatile var listRoute = true
    /** An error status for GET /push/targets (503: the server is down for a moment). */
    @Volatile var listStatus: Int? = null

    init {
        server.dispatcher = object : Dispatcher() {
            override fun dispatch(request: RecordedRequest): MockResponse {
                val path = request.path.orEmpty()
                val call = Call(request.method.orEmpty(), path, request.body.readUtf8())
                calls += call
                val p = path.substringBefore('?')
                return when {
                    p == "/api/v1/push/targets" && call.method == "GET" ->
                        if (listStatus != null) MockResponse().setResponseCode(listStatus!!).setBody("""{"detail":"down"}""")
                        else if (!listRoute) MockResponse().setResponseCode(404).setBody("""{"detail":"Not Found"}""")
                        else MockResponse().setBody("""{"events":["new_mail"],"items":[]""" +
                            (vapidKey?.let { ""","vapid_public_key":"$it"""" } ?: "") + "}")
                    p == "/api/v1/push/targets" && call.method == "POST" -> {
                        val ep = call.json["endpoint"].toString().trim('"')
                        val id = targets.getOrPut(ep) { nextId.incrementAndGet() }
                        MockResponse().setBody("""{"id":$id,"kind":"unifiedpush","endpoint":"$ep","device":"x","events":[],"encrypted":${call.json.containsKey("keys")}}""")
                    }
                    p.matches(Regex("/api/v1/push/targets/\\d+")) && call.method == "DELETE" -> {
                        val id = p.substringAfterLast('/').toLong()
                        if (targets.values.remove(id)) MockResponse().setBody("""{"ok":true}""")
                        else MockResponse().setResponseCode(404).setBody("""{"detail":"push target not found"}""")
                    }
                    p.matches(Regex("/api/v1/push/targets/\\d+/test")) -> MockResponse().setBody(testAnswer)
                    down -> MockResponse().setResponseCode(503).setBody("""{"detail":"down"}""")
                    p == "/api/v1/conversations" -> MockResponse().setBody(
                        """{"items":${MailrJson.encodeToString(ListSerializer(ConversationSummary.serializer()), conversations)}}""")
                    p == "/api/v1/approvals" -> MockResponse().setBody(
                        """{"items":${MailrJson.encodeToString(ListSerializer(Approval.serializer()), approvals)}}""")
                    p == "/api/v1/outbox" -> MockResponse().setBody(MailrJson.encodeToString(ListSerializer(Outbox.serializer()), failed))
                    p == "/api/v1/assistant/runs" -> MockResponse().setBody("""{"items":[]}""")
                    else -> MockResponse().setResponseCode(404).setBody("""{"detail":"not here"}""")
                }
            }
        }
        server.start()
    }

    fun url(): String = server.url("/").toString()
    fun posts(path: String) = calls.filter { it.method == "POST" && it.path == path }
    fun gets(path: String) = calls.filter { it.method == "GET" && it.path == path }
    fun deletes() = calls.filter { it.method == "DELETE" }.map { it.path.substringAfterLast('/').toLong() }
    fun shutdown() = server.shutdown()
}
