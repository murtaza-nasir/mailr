package io.github.murtazanasir.mailr.notesapp

import android.app.Application
import androidx.test.core.app.ApplicationProvider
import io.github.murtazanasir.mailr.data.api.ApiClient
import io.github.murtazanasir.mailr.data.api.ApiException
import io.github.murtazanasir.mailr.data.api.MailrJson
import io.github.murtazanasir.mailr.data.api.NotesTransport
import io.github.murtazanasir.mailr.data.api.ServerMessages
import io.github.murtazanasir.mailr.data.api.shownDetail
import io.github.murtazanasir.mailr.data.api.userMessage
import io.github.murtazanasir.mailr.data.auth.SessionStore
import io.github.murtazanasir.mailr.notes.data.NotesHttpException
import io.github.murtazanasir.mailr.notes.data.notesMessage
import kotlinx.coroutines.runBlocking
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.JsonPrimitive
import kotlinx.serialization.json.buildJsonObject
import kotlinx.serialization.json.jsonArray
import kotlinx.serialization.json.jsonObject
import kotlinx.serialization.json.jsonPrimitive
import kotlinx.serialization.json.put
import okhttp3.mockwebserver.Dispatcher
import okhttp3.mockwebserver.MockResponse
import okhttp3.mockwebserver.MockWebServer
import okhttp3.mockwebserver.RecordedRequest
import org.junit.After
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNotEquals
import org.junit.Assert.fail
import org.junit.Before
import org.junit.Test
import org.junit.runner.RunWith
import org.robolectric.RobolectricTestRunner
import org.robolectric.annotation.Config

/**
 * Error codes the server's routes raise, shown in another language. The codes and their English texts
 * come from server-errors-en.json (copies of backend/mailr/i18n/en.json); the "translation" is the
 * pseudo-language en-XA, made in the test as the web makes it. An error shows the catalogue's text with
 * its params filled in, through userMessage(), shownDetail() and the Notes transport.
 */
@RunWith(RobolectricTestRunner::class)
@Config(application = Application::class, sdk = [34])
class ServerErrorCodesTest {
    private val ctx get() = ApplicationProvider.getApplicationContext<Application>()
    private lateinit var server: MockWebServer
    private lateinit var client: ApiClient

    private val file = MailrJson.parseToJsonElement(
        javaClass.classLoader!!.getResource("server-errors-en.json")!!.readText()).jsonObject
    private val english: Map<String, String> =
        file["messages"]!!.jsonObject.mapValues { it.value.jsonPrimitive.content }
    private val cases: List<Pair<String, Map<String, String>>> = file["cases"]!!.jsonArray.map { c ->
        val o = c.jsonObject
        o["code"]!!.jsonPrimitive.content to o["params"]!!.jsonObject.mapValues { it.value.jsonPrimitive.content }
    }

    @Before fun setUp() {
        ServerMessages.init(ctx)
        ServerMessages.clear()
        val catalogue = buildJsonObject {
            put("lang", "xx")
            put("version", "pseudo")
            put("messages", buildJsonObject { english.forEach { (k, v) -> put(k, pseudo(v)) } })
        }.toString()
        server = MockWebServer()
        server.dispatcher = object : Dispatcher() {
            override fun dispatch(request: RecordedRequest): MockResponse {
                val path = request.requestUrl!!.encodedPath
                if (path == "/api/v1/i18n/messages") return MockResponse().setBody(catalogue)
                val i = path.removePrefix("/api/v1/case/").toIntOrNull() ?: return MockResponse().setResponseCode(404)
                val (code, params) = cases[i]
                val body = buildJsonObject {
                    put("detail", ServerMessages.interpolate(english.getValue(code), params))
                    put("code", code)
                    put("params", JsonObject(params.mapValues { JsonPrimitive(it.value) }))
                }
                return MockResponse().setResponseCode(400).setBody(body.toString())
            }
        }
        server.start()
        client = ApiClient(SessionStore(ctx).apply { this.server = this@ServerErrorCodesTest.server.url("/").toString().trimEnd('/') })
    }

    @After fun tearDown() {
        ServerMessages.clear()
        server.shutdown()
    }

    @Test fun convertedCodesShowTheCatalogueTextInTheAppsLanguage() = runBlocking {
        ServerMessages.refresh(client, "xx")
        cases.forEachIndexed { i, (code, params) ->
            val want = ServerMessages.interpolate(pseudo(english.getValue(code)), params)
            val e = failure { client.get("case/$i", JsonObject.serializer()) }
            assertEquals(code, e.messageCode)
            assertEquals(code, want, e.userMessage())
            assertEquals(code, want, e.shownDetail())
            assertNotEquals("the English detail is not what is shown", e.detail, e.userMessage())

            val n = try {
                NotesTransport(client).request("GET", "case/$i", null, null, emptyMap())
                fail("no NotesHttpException"); throw AssertionError()
            } catch (x: NotesHttpException) { x }
            assertEquals(code, want, n.notesMessage())
        }
    }

    @Test fun withoutTheCatalogueTheEnglishDetailIsShown() = runBlocking {
        val e = failure { client.get("case/0", JsonObject.serializer()) }
        assertEquals("another template already uses ;sig", e.shownDetail())
        assertEquals("another template already uses ;sig", e.userMessage())
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

    private companion object {
        val ACCENTS = "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ"
            .zip("áƀçðéƒĝĥíĵķļɱñóƥʠŕšţúṽŵẋýžÁƁÇÐÉƑĜĤÍĴĶĻṀÑÓƤǪŔŠŢÚṼŴẊÝŽ").toMap()
        val PART = Regex("""(\{\{\s*\w+\s*\}\})""")

        /** The web's en-XA (frontend/src/i18n/index.ts): accented, in brackets, placeholders kept. */
        fun pseudo(s: String): String {
            var letters = 0
            val out = StringBuilder()
            var at = 0
            fun plain(t: String) = t.forEach { c -> ACCENTS[c]?.let { letters++; out.append(it) } ?: out.append(c) }
            for (m in PART.findAll(s)) {
                plain(s.substring(at, m.range.first)); out.append(m.value); at = m.range.last + 1
            }
            plain(s.substring(at))
            return "[$out ${"•".repeat(maxOf(1, Math.round(letters * 0.35).toInt()))}]"
        }
    }
}
