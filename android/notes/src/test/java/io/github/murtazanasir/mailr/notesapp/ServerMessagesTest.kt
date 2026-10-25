package io.github.murtazanasir.mailr.notesapp

import android.app.Application
import androidx.test.core.app.ApplicationProvider
import io.github.murtazanasir.mailr.data.api.ApiClient
import io.github.murtazanasir.mailr.data.api.ApiException
import io.github.murtazanasir.mailr.data.api.ServerMessages
import io.github.murtazanasir.mailr.data.api.userMessage
import io.github.murtazanasir.mailr.data.auth.SessionStore
import kotlinx.coroutines.runBlocking
import kotlinx.serialization.json.JsonObject
import okhttp3.mockwebserver.Dispatcher
import okhttp3.mockwebserver.MockResponse
import okhttp3.mockwebserver.MockWebServer
import okhttp3.mockwebserver.RecordedRequest
import org.junit.After
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Assert.fail
import org.junit.Before
import org.junit.Test
import org.junit.runner.RunWith
import org.robolectric.RobolectricTestRunner
import org.robolectric.annotation.Config
import java.util.concurrent.atomic.AtomicInteger

/**
 * The server's error texts (ServerMessages): `{{name}}` filled from the error's params, a missing code
 * or placeholder, a refresh from GET /api/v1/i18n/messages (kept for a day per language, stored for the
 * next start), and an older server (404) or no server leaving the catalogue as it was.
 */
@RunWith(RobolectricTestRunner::class)
@Config(application = Application::class, sdk = [34])
class ServerMessagesTest {
    private val ctx get() = ApplicationProvider.getApplicationContext<Application>()
    private lateinit var server: MockWebServer
    private lateinit var client: ApiClient
    private val fetches = AtomicInteger()
    @Volatile private var catalogueStatus = 200
    @Volatile private var lastLang: String? = null

    private val catalogue = """{"lang":"fr","version":"abc123","messages":{
        "conversation.not_found":"Conversation introuvable.",
        "label.exists":"Le libellé {{name}} existe déjà ({{count}}).",
        "number":7}}"""

    @Before fun setUp() {
        ServerMessages.init(ctx)
        ServerMessages.clear()
        server = MockWebServer()
        server.dispatcher = object : Dispatcher() {
            override fun dispatch(request: RecordedRequest): MockResponse {
                val path = request.requestUrl!!.encodedPath
                return when (path) {
                    "/api/v1/i18n/messages" -> {
                        fetches.incrementAndGet()
                        lastLang = request.requestUrl!!.queryParameter("lang")
                        if (catalogueStatus == 200) MockResponse().setBody(catalogue)
                        else MockResponse().setResponseCode(catalogueStatus).setBody("""{"detail":"Not Found"}""")
                    }
                    "/api/v1/labels" -> MockResponse().setResponseCode(409)
                        .setBody("""{"detail":"Label Home exists.","code":"label.exists","params":{"name":"Home","count":3}}""")
                    "/api/v1/plain" -> MockResponse().setResponseCode(404).setBody("""{"detail":"Not found"}""")
                    "/api/v1/unknown" -> MockResponse().setResponseCode(400)
                        .setBody("""{"detail":"Something odd.","code":"odd.thing"}""")
                    else -> MockResponse().setResponseCode(404)
                }
            }
        }
        server.start()
        val sessions = SessionStore(ctx).apply { this.server = this@ServerMessagesTest.server.url("/").toString().trimEnd('/') }
        client = ApiClient(sessions)
    }

    @After fun tearDown() {
        ServerMessages.clear()
        server.shutdown()
    }

    @Test fun interpolationFillsEveryPlaceholderAndKeepsUnknownOnes() {
        assertEquals("Hi Ann, 3 new", ServerMessages.interpolate("Hi {{name}}, {{ n }} new", mapOf("name" to "Ann", "n" to "3")))
        assertEquals("Hi {{name}}", ServerMessages.interpolate("Hi {{name}}", emptyMap()))
        assertEquals("No placeholders", ServerMessages.interpolate("No placeholders", mapOf("x" to "y")))
    }

    @Test fun noCatalogueOrMissingKeyGivesNull() = runBlocking {
        assertNull(ServerMessages.text("conversation.not_found", emptyMap()))
        ServerMessages.refresh(client, "fr")
        assertNull(ServerMessages.text("no.such.code", emptyMap()))
        assertNull(ServerMessages.text(null, emptyMap()))
        // A value that is not text is left out of the catalogue.
        assertNull(ServerMessages.text("number", emptyMap()))
    }

    @Test fun refreshFetchesOncePerLanguageAndDayAndStoresIt() = runBlocking {
        ServerMessages.refresh(client, "fr", now = 1_000_000)
        assertEquals(1, fetches.get())
        assertEquals("fr", lastLang)
        assertEquals("abc123", ServerMessages.current.version)
        assertEquals("Le libellé Home existe déjà (3).", ServerMessages.text("label.exists", mapOf("name" to "Home", "count" to "3")))

        ServerMessages.refresh(client, "fr", now = 1_000_000 + 60_000)
        assertEquals("same language within a day: no fetch", 1, fetches.get())
        ServerMessages.refresh(client, "de", now = 1_000_000 + 60_000)
        assertEquals("another language: fetched", 2, fetches.get())
        assertEquals("de", lastLang)
        ServerMessages.refresh(client, "de", now = 1_000_000 + 60_000 + ServerMessages.MAX_AGE_MS)
        assertEquals("a day later: fetched again", 3, fetches.get())

        // The next start reads the stored catalogue before any network.
        val stored = ServerMessages.current
        ServerMessages.init(ctx)
        assertEquals(stored, ServerMessages.current)
        assertEquals("Conversation introuvable.", ServerMessages.text("conversation.not_found", emptyMap()))
    }

    @Test fun anOlderServerOrNoNetworkLeavesTheCatalogueAsItWas() = runBlocking {
        ServerMessages.refresh(client, "fr", now = 1_000_000)
        val before = ServerMessages.current

        catalogueStatus = 404
        ServerMessages.refresh(client, "de", now = 2_000_000)
        assertEquals(before, ServerMessages.current)

        server.shutdown()
        ServerMessages.refresh(client, "es", now = 3_000_000) // does not throw
        assertEquals(before, ServerMessages.current)

        ServerMessages.clear()
        catalogueStatus = 404
        ServerMessages.refresh(ApiClient(SessionStore(ctx).apply { this.server = "" }), "fr") // no server set
        assertNull(ServerMessages.current.lang)
    }

    @Test fun errorsCarryTheirCodeAndParamsAndShowTheServerText() = runBlocking {
        val e = failure { client.get("labels", JsonObject.serializer()) }
        assertEquals(409, e.code)
        assertEquals("label.exists", e.messageCode)
        assertEquals(mapOf("name" to "Home", "count" to "3"), e.params)
        // No catalogue yet: today's behaviour (the server's English detail).
        assertEquals("Label Home exists.", e.userMessage())

        ServerMessages.refresh(client, "fr")
        assertEquals("Le libellé Home existe déjà (3).", e.userMessage())

        val plain = failure { client.get("plain", JsonObject.serializer()) }
        assertNull(plain.messageCode)
        assertEquals(emptyMap<String, String>(), plain.params)
        assertEquals("No longer available.", plain.userMessage())

        // A code the catalogue does not know: the detail, as before.
        val unknown = failure { client.get("unknown", JsonObject.serializer()) }
        assertEquals("odd.thing", unknown.messageCode)
        assertEquals("Something odd.", unknown.userMessage())
    }

    private suspend fun failure(block: suspend () -> Unit): ApiException {
        try {
            block()
        } catch (e: ApiException) {
            return e
        }
        fail("no ApiException")
        throw AssertionError()
    }
}
