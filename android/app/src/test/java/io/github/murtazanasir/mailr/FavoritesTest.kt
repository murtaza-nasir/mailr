// ACT-SURFACES: android.drawer
package io.github.murtazanasir.mailr

import android.app.Application
import androidx.compose.material3.MaterialTheme
import androidx.compose.ui.test.assertIsEnabled
import androidx.compose.ui.test.assertIsNotEnabled
import io.github.murtazanasir.mailr.testing.createComposeRule
import androidx.compose.ui.test.longClick
import androidx.compose.ui.test.onAllNodesWithText
import androidx.compose.ui.test.onNodeWithText
import androidx.compose.ui.test.performClick
import androidx.compose.ui.test.performTouchInput
import androidx.compose.ui.test.assertCountEquals
import androidx.test.core.app.ApplicationProvider
import io.github.murtazanasir.mailr.data.api.ApiClient
import io.github.murtazanasir.mailr.data.api.MailrApi
import io.github.murtazanasir.mailr.data.auth.SessionStore
import io.github.murtazanasir.mailr.data.db.MailrDb
import io.github.murtazanasir.mailr.data.repo.MailRepository
import io.github.murtazanasir.mailr.data.repo.MailView
import io.github.murtazanasir.mailr.ui.mail.DEFAULT_FAVORITES
import io.github.murtazanasir.mailr.ui.mail.DrawerFavorites
import io.github.murtazanasir.mailr.ui.mail.MailDrawer
import io.github.murtazanasir.mailr.ui.mail.addFavorite
import io.github.murtazanasir.mailr.ui.mail.canFavorite
import io.github.murtazanasir.mailr.ui.mail.moveFavorite
import io.github.murtazanasir.mailr.ui.mail.removeFavorite
import io.github.murtazanasir.mailr.ui.mail.resolveFavorites
import io.github.murtazanasir.mailr.ui.mail.storedFavorites
import kotlinx.coroutines.test.runTest
import kotlinx.serialization.json.JsonArray
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.JsonPrimitive
import kotlinx.serialization.json.jsonArray
import kotlinx.serialization.json.jsonObject
import kotlinx.serialization.json.jsonPrimitive
import okhttp3.mockwebserver.Dispatcher
import okhttp3.mockwebserver.MockResponse
import okhttp3.mockwebserver.MockWebServer
import okhttp3.mockwebserver.RecordedRequest
import org.junit.After
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Before
import org.junit.Rule
import org.junit.Test
import org.junit.runner.RunWith
import org.robolectric.RobolectricTestRunner
import org.robolectric.annotation.Config
import java.util.concurrent.CopyOnWriteArrayList

/**
 * Favorites in the Android drawer (android.drawer): nav.favorite_add,
 * nav.favorite_remove and nav.favorite_reorder from the long-press menu, saved as the
 * nav_favorites setting.
 */
@RunWith(RobolectricTestRunner::class)
@Config(application = Application::class, sdk = [34], qualifiers = "w411dp-h1600dp")
class FavoritesTest {
    @get:Rule val compose = createComposeRule()

    private val labels = Fixtures.labels

    // ---------- the list (same rules as the web's lib/favorites.ts) ----------

    @Test fun keysAreTheWebsKeys() {
        assertTrue(canFavorite("inbox") && canFavorite("all") && canFavorite("category:social") && canFavorite("label:4503599627370497"))
        assertFalse(canFavorite("label:abc") || canFavorite("category:nope") || canFavorite("attachments") || canFavorite("search"))
    }

    @Test fun deletedLabelsDropOutAndTheDefaultApplies() {
        assertEquals(DEFAULT_FAVORITES, resolveFavorites(null, labels))
        // 99 is no label; 1 is the Inbox label (a role), not a user label; duplicates go.
        assertEquals(listOf("label:10", "inbox", "category:social"),
            resolveFavorites(listOf("label:10", "label:99", "label:1", "inbox", "inbox", "category:social", "x"), labels))
        // While the labels load, label favorites wait.
        assertEquals(listOf("inbox"), resolveFavorites(listOf("label:10", "inbox"), emptyList()))
        assertEquals(emptyList<String>(), resolveFavorites(emptyList(), labels))
    }

    @Test fun editsKeepTheOrder() {
        val list = listOf("inbox", "sent", "drafts")
        assertEquals(list + "label:10", addFavorite(list, "label:10")) // nav.favorite_add
        assertEquals(list, addFavorite(list, "sent"))
        assertEquals(listOf("inbox", "drafts"), removeFavorite(list, "sent")) // nav.favorite_remove
        assertEquals(listOf("sent", "inbox", "drafts"), moveFavorite(list, "sent", -1)) // nav.favorite_reorder
        assertEquals(listOf("inbox", "drafts", "sent"), moveFavorite(list, "sent", 1))
        assertEquals(list, moveFavorite(list, "inbox", -1))
        assertEquals(list, moveFavorite(list, "drafts", 1))
    }

    @Test fun storedListIsReadFromTheSettings() {
        assertNull(storedFavorites(JsonObject(emptyMap())))
        assertEquals(listOf("inbox", "label:10"),
            storedFavorites(JsonObject(mapOf("nav_favorites" to JsonArray(listOf(JsonPrimitive("inbox"), JsonPrimitive("label:10")))))))
    }

    // ---------- the drawer ----------

    private val calls = mutableListOf<String>()

    private fun drawer(keys: List<String>?, current: MailView = MailView()) = compose.setContent {
        MaterialTheme {
            MailDrawer(labels, current, null, emptyMap(), "me@example.com", 0, { v -> calls += "open ${v.view}" }, {}, {},
                favorites = DrawerFavorites(keys, { calls += "add $it" }, { calls += "remove $it" }, { k, d -> calls += "move $k $d" }))
        }
    }

    @Test fun favoritesSitAtTheTopWithTheirCounts() {
        drawer(listOf("label:10", "inbox", "drafts"))
        compose.onNodeWithText("Favourites").assertExists()
        // Research (2 unread), Inbox (7 unread) and Drafts (2) show in the list and again below.
        compose.onAllNodesWithText("Research").assertCountEquals(2)
        compose.onAllNodesWithText("7").assertCountEquals(2)
        compose.onAllNodesWithText("Drafts").assertCountEquals(2)
        val fav = compose.onAllNodesWithText("Research")[0].fetchSemanticsNode().boundsInRoot
        val inbox = compose.onAllNodesWithText("Inbox")[0].fetchSemanticsNode().boundsInRoot
        val starred = compose.onNodeWithText("Starred").fetchSemanticsNode().boundsInRoot
        assertTrue(fav.top < inbox.top && inbox.top < starred.top)
        compose.onAllNodesWithText("Research")[0].performClick()
        assertEquals(listOf("open label:10"), calls)
    }

    @Test fun emptyListShowsTheHint() {
        drawer(emptyList())
        compose.onNodeWithText("Favourites").assertExists()
        compose.onNodeWithText("Long-press a folder or label to add it here.").assertExists()
    }

    @Test fun noSectionUntilTheSettingsLoad() {
        drawer(null)
        compose.onAllNodesWithText("Favourites").assertCountEquals(0)
    }

    @Test fun longPressALabelAddsIt() {
        drawer(DEFAULT_FAVORITES)
        compose.onNodeWithText("Travel").performTouchInput { longClick() }
        compose.onNodeWithText("Add to Favourites").performClick()
        assertEquals(listOf("add label:13"), calls) // nav.favorite_add
    }

    @Test fun longPressAFavoriteRemovesOrMovesIt() {
        drawer(listOf("inbox", "label:10", "drafts"))
        compose.onAllNodesWithText("Research")[0].performTouchInput { longClick() }
        compose.onNodeWithText("Move up in Favourites").assertIsEnabled()
        compose.onNodeWithText("Move down in Favourites").assertIsEnabled().performClick()
        // The entry below has the same menu (one list).
        compose.onAllNodesWithText("Inbox")[0].performTouchInput { longClick() }
        compose.onNodeWithText("Move up in Favourites").assertIsNotEnabled()
        compose.onNodeWithText("Remove from Favourites").performClick()
        assertEquals(listOf("move label:10 1", "remove inbox"), calls) // nav.favorite_reorder, nav.favorite_remove
    }

    // ---------- saving (PATCH /me/settings) ----------

    private lateinit var server: MockWebServer
    private lateinit var db: MailrDb
    private lateinit var repo: MailRepository
    private val requests = CopyOnWriteArrayList<Pair<RecordedRequest, String>>()
    @Volatile private var patchStatus = 200

    @Before fun setUp() {
        server = MockWebServer()
        server.dispatcher = object : Dispatcher() {
            override fun dispatch(request: RecordedRequest): MockResponse {
                val body = request.body.readUtf8()
                requests += request to body
                val path = request.path.orEmpty()
                return when {
                    path == "/api/v1/me" -> MockResponse().setBody(
                        """{"user":{"id":1,"email":"me@example.com"},"settings":{"density":"comfortable","nav_favorites":["inbox","sent","drafts"]}}""")
                    path == "/api/v1/me/settings" && patchStatus == 200 -> MockResponse().setBody(
                        """{"density":"comfortable","nav_favorites":${body.substringAfter(":").removeSuffix("}")}}""")
                    path == "/api/v1/me/settings" -> MockResponse().setResponseCode(patchStatus).setBody("""{"detail":"nope"}""")
                    else -> MockResponse().setResponseCode(404)
                }
            }
        }
        server.start()
        val ctx = ApplicationProvider.getApplicationContext<Application>()
        val sessions = SessionStore(ctx).apply {
            server = this@FavoritesTest.server.url("/").toString()
            save("tok-123", "csrf-456", "me@example.com", "Me")
        }
        db = MailrDb.create(ctx, inMemory = true)
        repo = MailRepository(MailrApi(ApiClient(sessions)), db)
    }

    @After fun tearDown() {
        db.close()
        server.shutdown()
    }

    private fun shown() = storedFavorites(repo.me.value?.settings)

    @Test fun saveSendsTheWholeListAsTheSetting() = runTest {
        repo.refreshMe()
        repo.saveFavorites(listOf("label:10", "inbox", "sent", "drafts"))
        val (req, body) = requests.last()
        assertEquals("PATCH", req.method)
        assertEquals("/api/v1/me/settings", req.path)
        val sent = kotlinx.serialization.json.Json.parseToJsonElement(body).jsonObject
        assertEquals(setOf("nav_favorites"), sent.keys)
        assertEquals(listOf("label:10", "inbox", "sent", "drafts"), sent["nav_favorites"]!!.jsonArray.map { it.jsonPrimitive.content })
        assertEquals(listOf("label:10", "inbox", "sent", "drafts"), shown())
        // The other settings stay.
        assertEquals("comfortable", repo.me.value!!.settings["density"]!!.jsonPrimitive.content)
    }

    @Test fun refusedSavePutsTheOldListBack() = runTest {
        repo.refreshMe()
        patchStatus = 500
        val failed = runCatching { repo.saveFavorites(listOf("drafts")) }
        assertTrue(failed.isFailure)
        assertEquals(listOf("inbox", "sent", "drafts"), shown())
    }

    @Test fun aChangeOnTheWebShowsAfterTheNextRead() = runTest {
        repo.refreshMe()
        assertEquals(listOf("inbox", "sent", "drafts"), resolveFavorites(shown(), labels))
        // The drawer reads /me on resume and when it opens; the setting from the web replaces the list.
        server.dispatcher = object : Dispatcher() {
            override fun dispatch(request: RecordedRequest) = MockResponse().setBody(
                """{"user":{"id":1,"email":"me@example.com"},"settings":{"nav_favorites":["label:13","inbox"]}}""")
        }
        repo.refreshMe()
        assertEquals(listOf("label:13", "inbox"), resolveFavorites(shown(), labels))
    }
}
