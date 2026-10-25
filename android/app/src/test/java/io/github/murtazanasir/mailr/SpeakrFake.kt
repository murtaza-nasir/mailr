package io.github.murtazanasir.mailr

import android.app.Application
import androidx.test.core.app.ApplicationProvider
import io.github.murtazanasir.mailr.data.api.ApiClient
import io.github.murtazanasir.mailr.data.api.MailrJson
import io.github.murtazanasir.mailr.data.api.SpeakrApi
import io.github.murtazanasir.mailr.data.auth.SessionStore
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.jsonObject
import okhttp3.mockwebserver.Dispatcher
import okhttp3.mockwebserver.MockResponse
import okhttp3.mockwebserver.MockWebServer
import okhttp3.mockwebserver.RecordedRequest
import java.util.concurrent.CopyOnWriteArrayList

/**
 * mailr's /speakr routes as the Android tests need them: handlers by method and path prefix
 * (newest first); anything else answers 404. Every request is recorded, so a test can say that
 * nothing went to /speakr.
 */
class SpeakrFake {
    val server = MockWebServer()
    val requests = CopyOnWriteArrayList<Pair<RecordedRequest, String>>()
    private val handlers = CopyOnWriteArrayList<Triple<String, String, (RecordedRequest, String) -> MockResponse>>()
    lateinit var api: SpeakrApi
    lateinit var client: ApiClient

    fun on(method: String, path: String, respond: (RecordedRequest, String) -> MockResponse) {
        handlers.add(0, Triple(method, "/api/v1/$path", respond))
    }

    fun json(body: String, code: Int = 200): MockResponse =
        MockResponse().setResponseCode(code).setHeader("Content-Type", "application/json").setBody(body)

    fun start(): SpeakrFake {
        server.dispatcher = object : Dispatcher() {
            override fun dispatch(request: RecordedRequest): MockResponse {
                val body = request.body.readUtf8()
                requests += request to body
                val path = request.path.orEmpty()
                val h = handlers.firstOrNull { (m, p, _) -> m == request.method && (path == p || path.startsWith("$p?") || path.startsWith("$p/")) }
                return h?.third?.invoke(request, body) ?: json("""{"detail":"Not Found"}""", 404)
            }
        }
        server.start()
        val app = ApplicationProvider.getApplicationContext<Application>()
        val sessions = SessionStore(app).apply { server = this@SpeakrFake.server.url("/").toString(); save("tok", "csrf", "me@example.com", "Me") }
        client = ApiClient(sessions)
        api = SpeakrApi(client)
        return this
    }

    fun stop() {
        runCatching { server.shutdown() }
    }

    fun sent(method: String, path: String): List<Pair<RecordedRequest, String>> =
        requests.filter { it.first.method == method && it.first.path.orEmpty().startsWith("/api/v1/$path") }

    fun speakrRequests() = requests.filter { it.first.path.orEmpty().startsWith("/api/v1/speakr") }

    fun body(r: Pair<RecordedRequest, String>): JsonObject = MailrJson.parseToJsonElement(r.second).jsonObject

    companion object {
        const val RECORDING = """{"connector_id":3,"recording_id":12,"title":"Weekly sync","meeting_date":"2026-09-29T15:00:00+00:00",
            "duration_s":2730,"participants":["Dana Ortiz","Evan Li"],"tags":["Advising"],"folder":"Fall 26","status":"completed",
            "has_transcript":true,"has_summary":true,"audio_available":true,"is_archived":false,"local_only":true,
            "url":"https://speakr.example.org/recordings/12","cite":"[[r:12]]",
            "summary":"## Decisions\n\n- Move the **budget review** to Friday\n- Dana sends the Q3 numbers","notes":"Bring the draft.",
            "links":[{"id":41,"connector_id":3,"recording_id":12,"kind":"event","target":"ev-1","occurrence":"20260929T150000Z",
              "state":"suggested","score":0.82,"source":"auto","title":"Weekly sync","start":"2026-09-29T15:00:00+00:00"}],
            "candidates":[]}"""

        const val TRANSCRIPT = """{"recording_id":12,"connector_id":3,"title":"Weekly sync","segments":[
            {"t":0.0,"end":4.0,"speaker":"Dana Ortiz","text":"Let's start with the budget."},
            {"t":4.0,"end":9.0,"speaker":"Dana Ortiz","text":"The review moves to Friday."},
            {"t":754.0,"end":760.0,"speaker":"Evan Li","text":"I'll send the Q3 numbers by Thursday."}],
            "truncated":false,"total_segments":3,"timed":true,"url":"https://speakr.example.org/recordings/12"}"""

        const val FOLDERS = """[{"id":5,"name":"Fall 26","color":"#10B981"}]"""
        const val TAGS = """{"tags":[{"id":7,"name":"Advising","color":"#F59E0B","default_language":"en","default_min_speakers":2,
            "default_max_speakers":3,"default_hotwords":"FERPA, DATA"},{"id":8,"name":"Committee","color":"#3B82F6"}]}"""

        const val ACTION_ITEMS = """{"items":[
            {"text":"Send the Q3 numbers to Dana","owner":"me","mine":true,"due":"2026-10-02","t":754,"checked":true,"already_added":false,
             "url":"https://speakr.example.org/recordings/12?t=754"},
            {"text":"Book the room for Friday","owner":"Dana Ortiz","mine":false,"due":null,"t":null,"checked":false,"already_added":false,"url":""},
            {"text":"Share the agenda","owner":"me","mine":true,"due":null,"t":null,"checked":true,"already_added":true,"url":""}]}"""
    }
}
