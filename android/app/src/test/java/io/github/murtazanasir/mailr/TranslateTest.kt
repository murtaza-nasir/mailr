// ACT-SURFACES: android.reader
package io.github.murtazanasir.mailr

import android.app.Application
import android.os.Looper
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.SnackbarHostState
import androidx.compose.material3.Surface
import androidx.compose.ui.test.assertIsSelected
import androidx.compose.ui.test.onAllNodesWithText
import androidx.compose.ui.test.onFirst
import androidx.compose.ui.test.onNodeWithContentDescription
import androidx.compose.ui.test.onNodeWithTag
import androidx.compose.ui.test.onNodeWithText
import androidx.compose.ui.test.onRoot
import androidx.compose.ui.test.performClick
import androidx.compose.ui.Modifier
import androidx.compose.runtime.getValue
import androidx.compose.runtime.setValue
import androidx.compose.foundation.layout.fillMaxSize
import androidx.test.core.app.ApplicationProvider
import io.github.murtazanasir.mailr.data.api.Address
import io.github.murtazanasir.mailr.data.api.ConversationDetail
import io.github.murtazanasir.mailr.data.api.MailrJson
import io.github.murtazanasir.mailr.data.api.Message
import io.github.murtazanasir.mailr.data.auth.SessionStore
import io.github.murtazanasir.mailr.data.settings.ThemeMode
import io.github.murtazanasir.mailr.testing.createComposeRule
import io.github.murtazanasir.mailr.testing.eventually
import io.github.murtazanasir.mailr.ui.conversation.ConversationActions
import io.github.murtazanasir.mailr.ui.conversation.ConversationScreen
import io.github.murtazanasir.mailr.ui.conversation.ConversationViewModel
import io.github.murtazanasir.mailr.ui.conversation.ReaderExtrasUi
import io.github.murtazanasir.mailr.ui.conversation.TranslateLanguages
import io.github.murtazanasir.mailr.ui.conversation.TranslatePrefs
import io.github.murtazanasir.mailr.ui.conversation.Translation
import io.github.murtazanasir.mailr.ui.conversation.TranslationResult
import io.github.murtazanasir.mailr.ui.theme.MailrTheme
import com.github.takahirom.roborazzi.captureRoboImage
import kotlinx.serialization.json.jsonObject
import kotlinx.serialization.json.jsonPrimitive
import okhttp3.mockwebserver.Dispatcher
import okhttp3.mockwebserver.MockResponse
import okhttp3.mockwebserver.MockWebServer
import okhttp3.mockwebserver.RecordedRequest
import org.junit.After
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Assert.fail
import org.junit.Rule
import org.junit.Test
import org.junit.runner.RunWith
import org.robolectric.RobolectricTestRunner
import org.robolectric.Shadows.shadowOf
import org.robolectric.annotation.Config
import org.robolectric.annotation.GraphicsMode
import java.util.Locale
import java.util.concurrent.ConcurrentHashMap
import java.util.concurrent.CopyOnWriteArrayList

/**
 * mail.translate in the reader (ported from the web's Translate.tsx): Translate… in a message's menu
 * asks for a language with the last one first, the translation shows in place of the body with Show
 * original, and the server's plain refusal (local-only mail without a local model) shows as it comes.
 */
@RunWith(RobolectricTestRunner::class)
@GraphicsMode(GraphicsMode.Mode.NATIVE)
@Config(application = Application::class, sdk = [34], qualifiers = "w411dp-h891dp-xxhdpi")
class TranslateTest {
    @get:Rule(order = Int.MIN_VALUE) val graphs = TestGraph()
    @get:Rule val compose = createComposeRule()

    private val app: Application = ApplicationProvider.getApplicationContext()
    private var server: MockWebServer? = null
    private val requests = CopyOnWriteArrayList<RecordedRequest>()
    private val bodies = ConcurrentHashMap<RecordedRequest, String>()

    @After fun tearDown() {
        idle()
        graphs.close()
        server?.shutdown()
    }

    private fun idle() = shadowOf(Looper.getMainLooper()).idle()

    private val spanish = Message(801, 3066, Address("lucia@example.com", "Lucía Romero"), listOf(Address("me@example.com", "Alex")),
        subject = "Reunión del jueves", date = "2026-10-06T15:00:00Z", snippet = "Hola Alex, ¿podemos mover la reunión?",
        seen = true, offerTranslate = true)
    private val detail = ConversationDetail(Fixtures.detail.conversation.copy(subject = "Reunión del jueves"), listOf(spanish))

    private fun start(): AppGraph {
        val s = MockWebServer()
        s.dispatcher = object : Dispatcher() {
            override fun dispatch(request: RecordedRequest): MockResponse {
                bodies[request] = request.body.readUtf8()
                requests += request
                return when (request.path.orEmpty()) {
                    "/api/v1/conversations/3066" -> MockResponse().setBody(MailrJson.encodeToString(ConversationDetail.serializer(), detail))
                    "/api/v1/messages/801/translate" -> MockResponse().setBody(
                        """{"status":"ready","source":"Spanish","to":"English","text":"Hi Alex, can we move the meeting?","truncated":false}""")
                    // Local-only mail and no local model: the server's own words (intel/routes.py).
                    "/api/v1/messages/802/translate" -> MockResponse().setBody(
                        """{"status":"unavailable","reason":"No language model available for this message.","source":null,"to":"German","text":""}""")
                    "/api/v1/messages/803/translate" -> MockResponse().setResponseCode(502).setBody("""{"detail":"translation failed"}""")
                    else -> MockResponse().setResponseCode(404).setBody("""{"detail":"not here"}""")
                }
            }
        }
        s.start()
        server = s
        SessionStore(app).apply { server = s.url("/").toString(); save("tok", "csrf", "me@example.com", "Me") }
        return graphs.graph(app)
    }

    @Test fun mailTranslateAsksTheServerAndKeepsItsRefusal() {
        val g = start()
        val vm = graphs.vm { ConversationViewModel(g, 3066, null, null) }
        vm.extras.translate(801, "English")
        eventually("translated") { vm.extras.translations.value[801L] is Translation.Ready }
        val sent = requests.last { it.path == "/api/v1/messages/801/translate" }
        assertEquals("POST", sent.method)
        assertEquals("English", MailrJson.parseToJsonElement(bodies[sent]!!).jsonObject["to"]!!.jsonPrimitive.content)
        val r = (vm.extras.translations.value[801L] as Translation.Ready).result
        assertEquals("Spanish" to "Hi Alex, can we move the meeting?", r.source to r.text)
        vm.extras.showTranslation(801, false)
        assertEquals(false, (vm.extras.translations.value[801L] as Translation.Ready).showing)

        vm.extras.translate(802, "German")
        eventually("refused") { vm.extras.translations.value[802L] is Translation.Refused }
        assertEquals("No language model available for this message.", (vm.extras.translations.value[802L] as Translation.Refused).reason)
        vm.extras.translate(803, "French")
        eventually("failed") { vm.extras.translations.value[803L] is Translation.Failed }
    }

    @Test fun thePickerOffersTheLastLanguageFirstAndRemembersIt() {
        val prefs = TranslatePrefs(app)
        assertEquals(TranslateLanguages.phoneDefault(), prefs.last)
        prefs.last = "Japanese"
        assertEquals("Japanese", TranslatePrefs(app).last)
        assertEquals("Japanese", TranslateLanguages.ordered("Japanese").first())
        assertEquals(TranslateLanguages.all.size, TranslateLanguages.ordered("Japanese").size)
        assertEquals("German", TranslateLanguages.phoneDefault(Locale.GERMANY))
        assertEquals("Chinese (Traditional)", TranslateLanguages.phoneDefault(Locale.TAIWAN))
        assertEquals("English", TranslateLanguages.phoneDefault(Locale("xx")))
        // Every name passes the server's check (TranslateIn.to).
        assertTrue(TranslateLanguages.all.all { Regex("^[A-Za-z][A-Za-z \\-()]+$").matches(it) && it.length <= 40 })
        assertEquals("Translated from Spanish to English (first part).",
            TranslateLanguages.doneLine(TranslationResult(source = "Spanish", to = "English", truncated = true)))
    }

    private fun screen(extras: ReaderExtrasUi, picked: MutableList<String>, dark: Boolean = false) {
        compose.setContent {
            MailrTheme(if (dark) ThemeMode.DARK else ThemeMode.LIGHT, dynamic = false) {
                Surface(Modifier.fillMaxSize(), color = MaterialTheme.colorScheme.surface) {
                    ConversationScreen(detail, null, Fixtures.labels, setOf(801L), emptyMap(), null, null, tabletop = false,
                        snackbar = SnackbarHostState(), actions = ConversationActions(
                            back = null, act = {}, toggle = {}, expandAll = {}, star = { _, _ -> }, reply = { _, _ -> },
                            openAttachment = { _, _ -> }, showImages = {}, snooze = {}, labels = {}, summarize = {}, unsubscribe = {},
                            mailto = {}, retry = {},
                            translate = { m, lang -> picked += "${m.id}:$lang" },
                            showTranslation = { m, on -> picked += "${m.id}:show:$on" }), extras = extras)
                }
            }
        }
    }

    @Test fun translateFromTheMenuShowsTheTranslationWithShowOriginal() {
        val picked = mutableListOf<String>()
        screen(ReaderExtrasUi(translateTo = "French"), picked)
        compose.onNodeWithContentDescription("Message actions").performClick()
        compose.onNodeWithTag("message-translate").performClick()
        compose.onNodeWithText("Translate to").assertExists()
        // The last choice is first and selected; another one can be picked.
        compose.onAllNodesWithText("French").onFirst().assertIsSelected()
        compose.onNodeWithText("Spanish").performClick()
        compose.onNodeWithTag("translate-go").performClick()
        assertEquals(listOf("801:Spanish"), picked)
    }

    @Test fun theOfferBarTheTranslationAndTheRefusal() {
        val picked = mutableListOf<String>()
        val ready = TranslationResult(source = "Spanish", to = "English",
            text = "Hi Alex,\n\nCan we move Thursday's meeting to 3 pm? The room is booked until then.\n\nLucía")
        var extras by androidx.compose.runtime.mutableStateOf(ReaderExtrasUi(translations = mapOf(801L to Translation.Ready(ready))))
        compose.setContent {
            MailrTheme(ThemeMode.LIGHT, dynamic = false) {
                Surface(Modifier.fillMaxSize(), color = MaterialTheme.colorScheme.surface) {
                    ConversationScreen(detail, null, Fixtures.labels, setOf(801L), emptyMap(), null, null, tabletop = false,
                        snackbar = SnackbarHostState(), actions = ConversationActions(
                            back = null, act = {}, toggle = {}, expandAll = {}, star = { _, _ -> }, reply = { _, _ -> },
                            openAttachment = { _, _ -> }, showImages = {}, snooze = {}, labels = {}, summarize = {}, unsubscribe = {},
                            mailto = {}, retry = {},
                            translate = { m, lang -> picked += "${m.id}:$lang" },
                            showTranslation = { m, on -> picked += "${m.id}:show:$on" }), extras = extras)
                }
            }
        }
        compose.onNodeWithTag("translated-body").assertExists()
        compose.onNodeWithText("Translated from Spanish to English.").assertExists()
        compose.onRoot().captureRoboImage("screenshots/reader-translated-light.png")
        compose.onNodeWithText("Show original").performClick()
        assertEquals(listOf("801:show:false"), picked)

        extras = ReaderExtrasUi(translations = mapOf(801L to Translation.Refused("German", "No language model available for this message.")))
        compose.onNodeWithText("No language model available for this message.").assertExists()
        compose.onNodeWithTag("translated-body").assertDoesNotExist()
        compose.onRoot().captureRoboImage("screenshots/reader-translate-refused-light.png")
        extras = ReaderExtrasUi(translations = mapOf(801L to Translation.Failed("German", "x")))
        compose.onNodeWithText("Try again").performClick()
        assertEquals("801:German", picked.last())
    }
}
