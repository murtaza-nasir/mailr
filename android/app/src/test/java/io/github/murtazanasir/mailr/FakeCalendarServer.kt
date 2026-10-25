package io.github.murtazanasir.mailr

import io.github.murtazanasir.mailr.time.AppClock
import android.app.Application
import androidx.test.core.app.ApplicationProvider
import io.github.murtazanasir.mailr.data.api.ApiClient
import io.github.murtazanasir.mailr.data.api.CalendarApi
import io.github.murtazanasir.mailr.data.api.CalendarEvent
import io.github.murtazanasir.mailr.data.api.MailrApi
import io.github.murtazanasir.mailr.data.api.MailrJson
import io.github.murtazanasir.mailr.data.api.TaskItem
import io.github.murtazanasir.mailr.data.auth.SessionStore
import io.github.murtazanasir.mailr.data.cal.CalendarRepository
import io.github.murtazanasir.mailr.data.db.MailrDb
import io.github.murtazanasir.mailr.data.repo.MailRepository
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.JsonPrimitive
import kotlinx.serialization.json.jsonObject
import okhttp3.mockwebserver.Dispatcher
import okhttp3.mockwebserver.MockResponse
import okhttp3.mockwebserver.MockWebServer
import okhttp3.mockwebserver.RecordedRequest
import java.time.ZoneId
import java.time.ZoneOffset
import java.util.concurrent.CopyOnWriteArrayList

/**
 * A scriptable mailr server for the calendar data layer (the B1/B2 routes may not exist yet):
 * handlers match a method and a path prefix under /api/v1, the newest first. Unmatched reads
 * answer 404 and unmatched writes `{"ok":true}`, as a server before the calendar build would.
 */
class FakeCalendarServer {
    val server = MockWebServer()
    val requests = CopyOnWriteArrayList<Pair<RecordedRequest, String>>()
    private val handlers = CopyOnWriteArrayList<Triple<String, String, (RecordedRequest, String) -> MockResponse>>()

    lateinit var db: MailrDb
    lateinit var mail: MailRepository
    lateinit var repo: CalendarRepository
    var zone: ZoneId = ZoneOffset.UTC
    var now: Long = AppClock.millis()

    fun on(method: String, path: String, respond: (RecordedRequest, String) -> MockResponse) {
        handlers.add(0, Triple(method, "/api/v1/$path", respond))
    }

    fun json(body: String, code: Int = 200): MockResponse =
        MockResponse().setResponseCode(code).setHeader("Content-Type", "application/json").setBody(body)

    fun start(): FakeCalendarServer {
        server.dispatcher = object : Dispatcher() {
            override fun dispatch(request: RecordedRequest): MockResponse {
                val body = request.body.readUtf8()
                requests += request to body
                val path = request.path.orEmpty()
                val h = handlers.firstOrNull { (m, p, _) -> m == request.method && (path == p || path.startsWith("$p?") || path.startsWith("$p/")) }
                if (h != null) return h.third(request, body)
                return if (request.method == "GET") json("""{"detail":"Not Found"}""", 404) else json("""{"ok":true}""")
            }
        }
        server.start()
        val app = ApplicationProvider.getApplicationContext<Application>()
        val sessions = SessionStore(app).apply { server = this@FakeCalendarServer.server.url("/").toString(); save("tok", "csrf", "me@example.com", "Me") }
        db = MailrDb.create(app, inMemory = true)
        val client = ApiClient(sessions)
        mail = MailRepository(MailrApi(client), db)
        repo = CalendarRepository(CalendarApi(client), db, mail, now = { now }, zone = { zone })
        return this
    }

    fun stop() {
        db.close()
        runCatching { server.shutdown() }
    }

    /** Requests [method] to [path] (prefix, under /api/v1), in order. */
    fun sent(method: String, path: String): List<Pair<RecordedRequest, String>> =
        requests.filter { it.first.method == method && it.first.path.orEmpty().startsWith("/api/v1/$path") }

    fun body(r: Pair<RecordedRequest, String>): JsonObject = MailrJson.parseToJsonElement(r.second).jsonObject

    companion object {
        fun eventJson(e: CalendarEvent): JsonObject =
            JsonObject(MailrJson.encodeToJsonElement(CalendarEvent.serializer(), e).jsonObject + ("kind" to JsonPrimitive("event")))

        fun taskJson(t: TaskItem): JsonObject =
            JsonObject(MailrJson.encodeToJsonElement(TaskItem.serializer(), t).jsonObject + ("kind" to JsonPrimitive("task")))
    }
}
