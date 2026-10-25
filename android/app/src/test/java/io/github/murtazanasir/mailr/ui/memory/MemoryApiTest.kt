// ACT-SURFACES: android.settings
package io.github.murtazanasir.mailr.ui.memory

import android.app.Application
import android.os.Looper
import androidx.test.core.app.ApplicationProvider
import io.github.murtazanasir.mailr.TestGraph
import io.github.murtazanasir.mailr.data.api.MailrJson
import io.github.murtazanasir.mailr.data.api.Memory
import io.github.murtazanasir.mailr.data.api.MemoryEdit
import io.github.murtazanasir.mailr.data.api.MemoryRef
import io.github.murtazanasir.mailr.data.api.MemorySubject
import io.github.murtazanasir.mailr.data.auth.SessionStore
import io.github.murtazanasir.mailr.testing.eventually
import kotlinx.coroutines.runBlocking
import kotlinx.serialization.json.JsonNull
import kotlinx.serialization.json.jsonArray
import kotlinx.serialization.json.jsonObject
import kotlinx.serialization.json.jsonPrimitive
import kotlinx.serialization.json.long
import okhttp3.mockwebserver.Dispatcher
import okhttp3.mockwebserver.MockResponse
import okhttp3.mockwebserver.MockWebServer
import okhttp3.mockwebserver.RecordedRequest
import org.junit.After
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Rule
import org.junit.Test
import org.junit.runner.RunWith
import org.robolectric.RobolectricTestRunner
import org.robolectric.Shadows.shadowOf
import org.robolectric.annotation.Config
import java.util.concurrent.ConcurrentHashMap
import java.util.concurrent.CopyOnWriteArrayList

private const val LIST = """{"items":[
 {"id":13,"text":"Priya runs the Thursday design review","kind":"fact","entity_id":2,
  "entity":{"id":2,"name":"Priya Shah","kind":"person","is_self":false},"source":"extracted","status":"active","confidence":null,
  "refs":[{"message_id":501,"conversation_id":3066},{"chat_session_id":"x"}],"sensitive":false,"pinned":false,"expires_at":null,
  "use_count":3,"last_used_at":null,"created_at":"2026-09-30T14:00:00+00:00","updated_at":"2026-09-30T14:00:00+00:00","new_field":1}],
 "counts":{"active":1,"suggested":0}}"""

private const val PERSON = """{"email":"priya.shah@northwind.example","name":"Priya Shah","organization":"Northwind Studio","title":"Art director",
 "received_count":22,"sent_count":14,"last_seen":"2026-10-01T21:20:00+00:00","phones":[{"value":"+1 312 555 0142","type":"mobile","source":"carddav"}],
 "addresses":[{"text":"1 Market St, Chicago"}],"birthday":"--04-12","groups":[{"id":7,"name":"Studio team","colour":null,"source":"manual"}],
 "suggestions":[],"recent_conversations":[],"knowledge":{"entity":{"id":2,"kind":"person","name":"Priya Shah","is_self":false},"memories":[]}}"""

/**
 * The Memory page's requests are the web's: GET /assistant/memories?status=…, PATCH and POST with the
 * fields the web sends (memory.add), POST …/accept and …/reject (memory.accept_suggestion,
 * memory.dismiss_suggestion), POST /assistant/memories/learn (memory.learn_now), GET /people/{email}
 * for the card (person.card), and Create task (memory.make_task) through POST /tasks with the email linked.
 */
@RunWith(RobolectricTestRunner::class)
@Config(application = Application::class, sdk = [34])
class MemoryApiTest {
    @get:Rule(order = Int.MIN_VALUE) val graphs = TestGraph()

    private val app: Application = ApplicationProvider.getApplicationContext()
    private var server: MockWebServer? = null
    private val requests = CopyOnWriteArrayList<RecordedRequest>()
    private val bodies = ConcurrentHashMap<RecordedRequest, String>()

    @After fun tearDown() {
        shadowOf(Looper.getMainLooper()).idle()
        graphs.close()
        server?.shutdown()
    }

    private fun store(): ApiMemoryStore {
        val s = MockWebServer()
        s.dispatcher = object : Dispatcher() {
            override fun dispatch(request: RecordedRequest): MockResponse {
                bodies[request] = request.body.readUtf8()
                requests += request
                val path = request.path.orEmpty()
                return when {
                    path.startsWith("/api/v1/assistant/memories?") -> MockResponse().setBody(LIST)
                    path == "/api/v1/people/priya.shah@northwind.example" -> MockResponse().setBody(PERSON)
                    path == "/api/v1/tasks" -> MockResponse().setBody("""{"uid":"t-1","summary":"x"}""")
                    else -> MockResponse().setBody("""{"ok":true}""")
                }
            }
        }
        s.start()
        server = s
        SessionStore(app).apply { server = s.url("/").toString(); save("tok", "csrf", "me@example.com", "Me") }
        return ApiMemoryStore(graphs.graph(app))
    }

    private fun body(method: String, path: String) =
        MailrJson.parseToJsonElement(bodies[requests.last { it.method == method && it.path == path }]!!).jsonObject

    @Test fun readsAndWritesTheWebsEndpoints() = runBlocking {
        val st = store()
        val list = st.list("active")
        val m = list.items.single()
        assertEquals("Priya Shah", m.entity?.name)
        assertEquals(3066L, m.conversationId)
        assertTrue(requests.last().path!!.contains("status=active"))

        st.save(13, MemoryEdit("Priya runs the Friday review", "fact", MemorySubject.Known(2), pinned = true))
        val patch = body("PATCH", "/api/v1/assistant/memories/13")
        assertEquals("Priya runs the Friday review", patch["text"]!!.jsonPrimitive.content)
        assertEquals(2L, patch["entity_id"]!!.jsonPrimitive.long)
        assertEquals("true", patch["pinned"]!!.jsonPrimitive.content)

        st.save(null, MemoryEdit("You are vegetarian", "preference", MemorySubject.You))
        val post = body("POST", "/api/v1/assistant/memories")
        assertEquals("me", post["about"]!!.jsonPrimitive.content)
        assertEquals(JsonNull, post["entity_id"])

        st.save(null, MemoryEdit("Sam does the books", "fact", MemorySubject.New("Sam Okafor", "person", "sam@example.org")))
        val about = body("POST", "/api/v1/assistant/memories")["about"]!!.jsonObject
        assertEquals("Sam Okafor", about["name"]!!.jsonPrimitive.content)
        assertEquals("sam@example.org", about["emails"]!!.jsonArray.single().jsonPrimitive.content)

        st.accept(21); st.dismiss(22); st.learn(); st.delete(13)
        val paths = requests.map { "${it.method} ${it.path}" }
        assertTrue(paths.containsAll(listOf("POST /api/v1/assistant/memories/21/accept", "POST /api/v1/assistant/memories/22/reject",
            "POST /api/v1/assistant/memories/learn", "DELETE /api/v1/assistant/memories/13")))

        val card = st.person("Priya.Shah@northwind.example")
        assertEquals("Art director", card.title)
        assertEquals("Studio team", card.groups.single().name)
        assertEquals("mobile", card.phones.single().type)
        assertEquals(2L, card.knowledge.entity?.id)
    }

    @Test fun aCommitmentBecomesATaskLinkedToItsEmail() = runBlocking {
        val st = store()
        st.makeTask(Memory(14, "You promised Priya the brand guide by Friday.", "commitment", refs = listOf(MemoryRef(501, 3066))))
        eventually("POST /api/v1/tasks") { requests.any { it.method == "POST" && it.path == "/api/v1/tasks" } }
        val task = body("POST", "/api/v1/tasks")
        assertEquals("Promised Priya the brand guide by Friday", task["summary"]!!.jsonPrimitive.content)
        assertEquals(3066L, task["conversation_id"]!!.jsonPrimitive.long)
    }
}
