// ACT-SURFACES: android.list android.reader
package io.github.murtazanasir.mailr

import android.app.Application
import android.os.Looper
import androidx.compose.ui.semantics.SemanticsProperties
import androidx.compose.ui.test.SemanticsMatcher
import androidx.compose.ui.test.assert
import io.github.murtazanasir.mailr.testing.createComposeRule
import androidx.compose.ui.test.onNodeWithText
import androidx.compose.ui.test.performClick
import androidx.test.core.app.ApplicationProvider
import io.github.murtazanasir.mailr.data.api.Label
import io.github.murtazanasir.mailr.data.api.MailrJson
import io.github.murtazanasir.mailr.data.api.ShowInTabChoices
import io.github.murtazanasir.mailr.data.api.ShowInTabTarget
import io.github.murtazanasir.mailr.data.auth.SessionStore
import io.github.murtazanasir.mailr.testing.eventually
import io.github.murtazanasir.mailr.ui.mail.CategoryList
import io.github.murtazanasir.mailr.ui.mail.MailboxViewModel
import io.github.murtazanasir.mailr.ui.mail.ShowInTabList
import io.github.murtazanasir.mailr.ui.mail.TabMenus
import kotlinx.coroutines.runBlocking
import kotlinx.serialization.builtins.ListSerializer
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.jsonObject
import kotlinx.serialization.json.jsonPrimitive
import okhttp3.mockwebserver.Dispatcher
import okhttp3.mockwebserver.MockResponse
import okhttp3.mockwebserver.MockWebServer
import okhttp3.mockwebserver.RecordedRequest
import org.junit.After
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNotNull
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Assert.fail
import org.junit.Before
import org.junit.Rule
import org.junit.Test
import org.junit.runner.RunWith
import org.robolectric.RobolectricTestRunner
import org.robolectric.Shadows.shadowOf
import org.robolectric.annotation.Config
import java.util.concurrent.CopyOnWriteArrayList

/**
 * Show in tab (mail.show_in_tab) and Category (mail.category) on Android, in the list's selection
 * menu (android.list) and the reader's menu (android.reader): both open the sheets of
 * ui/mail/TabMenus.kt, whose choices come from GET layouts/show-in-tab and whose picks go through
 * MailboxViewModel to POST conversations/actions. The Undo sends the server's restore_tab record back
 * unchanged.
 */
@RunWith(RobolectricTestRunner::class)
@Config(application = Application::class, sdk = [34])
class ShowInTabTest {
    @get:Rule(order = Int.MIN_VALUE) val graphs = TestGraph()
    @get:Rule val compose = createComposeRule()
    private lateinit var server: MockWebServer
    private lateinit var g: AppGraph
    private val requests = CopyOnWriteArrayList<Pair<RecordedRequest, String>>()

    private val choicesJson = """{"layout":"mine","current":"inbox","tabs":[
        {"id":"inbox","name":"Inbox","icon":"inbox","kind":"first","label_id":null,"category":null,"reason":null},
        {"id":"github","name":"Github","icon":"tag","kind":"label","label_id":12,"category":null,"reason":null},
        {"id":"promotions","name":"Promotions","icon":"tag","kind":"category","label_id":null,"category":"promotions","reason":null},
        {"id":"boss","name":"Boss","icon":"star","kind":"disabled","label_id":null,"category":null,
         "reason":"This tab shows the results of a search. Mail shows here when it matches the search."}],
        "placements":[{"conversation_id":3075,"tab":"inbox"}]}"""
    private val undo = """{"action":"restore_tab","labels":{"901":[1,12]},"categories":{"message_ids":[901],
        "categories":["promotions"],"decisions":[5],"rejected":[]},"ids":[3075],"by":"conversation"}"""

    @Before fun setUp() {
        server = MockWebServer()
        server.dispatcher = object : Dispatcher() {
            override fun dispatch(request: RecordedRequest): MockResponse {
                requests += request to request.body.readUtf8()
                val path = request.path.orEmpty()
                return when {
                    path == "/api/v1/me" -> MockResponse().setBody("""{"user":{"id":1,"email":"me@example.com","name":"Me"},"identities":[]}""")
                    path.startsWith("/api/v1/labels") -> MockResponse().setBody(
                        MailrJson.encodeToString(ListSerializer(Label.serializer()), Fixtures.labels))
                    path.startsWith("/api/v1/layouts/show-in-tab") -> MockResponse().setBody(choicesJson)
                    path.startsWith("/api/v1/conversations/actions") -> MockResponse().setBody("""{"ok":true,"affected":1,"undo":$undo}""")
                    else -> MockResponse().setResponseCode(404).setBody("""{"detail":"not here"}""")
                }
            }
        }
        server.start()
        val ctx = ApplicationProvider.getApplicationContext<Application>()
        SessionStore(ctx).apply {
            server = this@ShowInTabTest.server.url("/").toString()
            save("tok-123", "csrf-456", "me@example.com", "Me")
        }
        g = graphs.graph(ctx)
    }

    @After fun tearDown() {
        shadowOf(Looper.getMainLooper()).idle()
        graphs.close()
        server.shutdown()
    }

    private fun actions(name: String): List<JsonObject> = requests
        .filter { it.first.path == "/api/v1/conversations/actions" }
        .map { MailrJson.parseToJsonElement(it.second).jsonObject }
        .filter { it["action"]?.jsonPrimitive?.content == name }

    /** mail.show_in_tab: the sheet's choices come from the server, with its one current tab. */
    @Test fun choicesComeFromTheServer() {
        val c = runBlocking { g.mail.showInTabChoices(listOf(3075L, 3074L)) }
        assertEquals(listOf("Inbox", "Github", "Promotions", "Boss"), c.tabs.map { it.name })
        assertEquals("inbox", c.current)
        assertTrue(c.tabs.last().disabled)
        assertEquals(12L, c.tabs[1].labelId)
        val r = requests.first { it.first.path.orEmpty().startsWith("/api/v1/layouts/show-in-tab") }.first
        assertEquals("3075,3074", r.requestUrl!!.queryParameter("conversation_ids"))
    }

    /** mail.show_in_tab on android.list and android.reader: one action naming the tab; Undo sends restore_tab back whole. */
    @Test fun pickingATabSendsOneActionAndUndoSendsTheRecordBack() {
        val vm = graphs.vm { MailboxViewModel(g) }
        vm.showInTab(listOf(3075L), ShowInTabTarget("github", "Github", kind = "label", labelId = 12))
        eventually("show_in_tab") { actions("show_in_tab").isNotEmpty() }
        val sent = actions("show_in_tab").single()
        assertEquals("github", sent["tab"]!!.jsonPrimitive.content)

        val out = runBlocking { g.mail.act(listOf(3075L), "show_in_tab", tab = "github") }
        val u = out.undo
        assertNotNull(u)
        assertEquals("restore_tab", u!!.action)
        assertTrue(runBlocking { g.mail.undo(u) })
        val back = actions("restore_tab").single()
        assertEquals(MailrJson.parseToJsonElement(undo).jsonObject["categories"], back["categories"])
        assertEquals(MailrJson.parseToJsonElement(undo).jsonObject["labels"], back["labels"])
    }

    /** mail.category on android.list and android.reader: the Category sheet sets the raw category. */
    @Test fun categorySetsTheRawCategory() {
        val vm = graphs.vm { MailboxViewModel(g) }
        vm.setCategory(listOf(3075L, 3074L), "social")
        eventually("category") { actions("category").isNotEmpty() }
        assertEquals("social", actions("category").single()["category"]!!.jsonPrimitive.content)
    }

    @Test fun helpers() {
        assertEquals("primary", TabMenus.sharedCategory(listOf(null)))
        assertEquals("social", TabMenus.sharedCategory(listOf("social", "social")))
        assertNull(TabMenus.sharedCategory(listOf("social", null)))
        assertNull(TabMenus.sharedCategory(emptyList()))
        assertFalse(TabMenus.offersShowInTab(1))
        assertTrue(TabMenus.offersShowInTab(2))
        assertEquals("Shown in Github.", TabMenus.shownIn("Github", 1))
        assertEquals("3 conversations shown in Github.", TabMenus.shownIn("Github", 3))
        assertEquals(listOf("Primary", "Promotions", "Social", "Updates", "Forums", "Purchases"), TabMenus.CATEGORIES.map { it.second })
    }

    /** The sheet: one mark, a disabled search tab with its reason, and a pick of an enabled tab. */
    @Test fun sheetMarksOneTabAndExplainsTheDisabledOne() {
        val choices = MailrJson.decodeFromString(ShowInTabChoices.serializer(), choicesJson)
        var picked: String? = null
        compose.setContent { ShowInTabList(choices) { picked = it.id } }
        val selected = SemanticsMatcher.expectValue(SemanticsProperties.Selected, true)
        val notSelected = SemanticsMatcher.expectValue(SemanticsProperties.Selected, false)
        compose.onNodeWithText("Inbox", useUnmergedTree = false).assert(selected)
        compose.onNodeWithText("Github").assert(notSelected)
        compose.onNodeWithText("This tab shows the results of a search. Mail shows here when it matches the search.").assertExists()
        compose.onNodeWithText("Boss").performClick()
        assertNull(picked)
        compose.onNodeWithText("Github").performClick()
        assertEquals("github", picked)
    }

    @Test fun categorySheetMarksOne() {
        var picked: String? = null
        compose.setContent { CategoryList("promotions") { picked = it } }
        compose.onNodeWithText("Promotions").assert(SemanticsMatcher.expectValue(SemanticsProperties.Selected, true))
        compose.onNodeWithText("Primary").assert(SemanticsMatcher.expectValue(SemanticsProperties.Selected, false))
        compose.onNodeWithText("Forums").performClick()
        assertEquals("forums", picked)
    }
}
