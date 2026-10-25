// ACT-SURFACES: android.drawer
package io.github.murtazanasir.mailr

import android.app.Application
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.material3.Surface
import androidx.compose.ui.Modifier
import androidx.compose.ui.test.assertCountEquals
import androidx.compose.ui.test.assertIsNotEnabled
import androidx.compose.ui.test.hasScrollAction
import androidx.compose.ui.test.hasTestTag
import androidx.compose.ui.test.hasText
import androidx.compose.ui.test.isDialog
import androidx.compose.ui.test.longClick
import androidx.compose.ui.test.onAllNodesWithText
import androidx.compose.ui.test.onNodeWithTag
import androidx.compose.ui.test.onNodeWithText
import androidx.compose.ui.test.performClick
import androidx.compose.ui.test.performScrollToNode
import androidx.compose.ui.test.performTextInput
import androidx.compose.ui.test.performTouchInput
import androidx.test.core.app.ApplicationProvider
import io.github.murtazanasir.mailr.data.api.ApiClient
import io.github.murtazanasir.mailr.data.api.ApiException
import io.github.murtazanasir.mailr.data.api.Label
import io.github.murtazanasir.mailr.data.api.MailAccountInfo
import io.github.murtazanasir.mailr.data.api.MailrApi
import io.github.murtazanasir.mailr.data.api.ViewCount
import io.github.murtazanasir.mailr.data.auth.SessionStore
import io.github.murtazanasir.mailr.data.db.MailrDb
import io.github.murtazanasir.mailr.data.repo.MailRepository
import io.github.murtazanasir.mailr.data.repo.MailView
import io.github.murtazanasir.mailr.data.settings.ThemeMode
import io.github.murtazanasir.mailr.testing.createComposeRule
import io.github.murtazanasir.mailr.testing.until
import io.github.murtazanasir.mailr.ui.mail.AccountFolderPrefs
import io.github.murtazanasir.mailr.ui.mail.DrawerAccountPins
import io.github.murtazanasir.mailr.ui.mail.MailDrawer
import io.github.murtazanasir.mailr.ui.mail.NewFolderDialog
import io.github.murtazanasir.mailr.ui.mail.accountFolderPrefs
import io.github.murtazanasir.mailr.ui.mail.accountLabelView
import io.github.murtazanasir.mailr.ui.mail.accountView
import io.github.murtazanasir.mailr.ui.mail.folderNameTaken
import io.github.murtazanasir.mailr.ui.mail.newFolderName
import io.github.murtazanasir.mailr.ui.theme.MailrTheme
import com.github.takahirom.roborazzi.captureRoboImage
import kotlinx.coroutines.test.runTest
import kotlinx.serialization.json.Json
import kotlinx.serialization.json.jsonObject
import kotlinx.serialization.json.jsonPrimitive
import kotlinx.serialization.json.long
import okhttp3.mockwebserver.Dispatcher
import okhttp3.mockwebserver.MockResponse
import okhttp3.mockwebserver.MockWebServer
import okhttp3.mockwebserver.RecordedRequest
import org.junit.After
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Before
import org.junit.Rule
import org.junit.Test
import org.junit.runner.RunWith
import org.robolectric.RobolectricTestRunner
import org.robolectric.annotation.Config
import org.robolectric.annotation.GraphicsMode
import java.util.concurrent.CopyOnWriteArrayList

/**
 * Folders made from the drawer (ui/mail/NewFolderDialog.kt): an account's heading
 * has ⋮ with "New folder" (a long press on the heading opens it too), and a folder's long press offers
 * "New folder inside". The dialog takes a name and, optionally, the folder it goes inside, and POSTs
 * /labels with the account as the label's home (ACT mail.account_folder_create); the server pins it to
 * the group, and the new folder opens. Screenshots: app/screenshots/new-folder-dialog-<theme>.png
 */
@RunWith(RobolectricTestRunner::class)
@GraphicsMode(GraphicsMode.Mode.NATIVE)
@Config(application = Application::class, sdk = [34], qualifiers = "w411dp-h2400dp-xxhdpi")
class NewFolderTest {
    @get:Rule val compose = createComposeRule()

    private val home = MailAccountInfo(1, "me@example.com", "Home", "blue")
    private val work = MailAccountInfo(2, "work@example.org", "Work", "orange")
    private val labels = Fixtures.labels + Label(20, "Invoices", color = "#188038", homeAccountId = 2)
    private val counts = mapOf(
        accountView(2, "inbox") to ViewCount(1, 3), accountView(1, "inbox") to ViewCount(0, 4),
        accountLabelView(2, 13) to ViewCount(0, 2),
    )

    @Test fun theNameIsThePathInsideItsParent() {
        assertEquals("Receipts", newFolderName("  Receipts ", null))
        assertEquals("Travel/2027", newFolderName("2027", "Travel"))
        assertEquals("Travel/2027", newFolderName("/2027/", "Travel"))
        assertEquals("", newFolderName("  ", "Travel"))
        assertTrue(folderNameTaken("travel", labels))
        assertTrue(folderNameTaken("Research/pads 2027", labels))
        assertFalse(folderNameTaken("Travel/2027", labels))
        assertFalse(folderNameTaken("", labels))
    }

    // ---------- the drawer ----------

    private val made = CopyOnWriteArrayList<String>()
    private var opened: MailView? = null
    @Volatile private var refuse: String? = null

    private fun drawer(dark: Boolean = false) {
        compose.setContent {
            MailrTheme(if (dark) ThemeMode.DARK else ThemeMode.LIGHT, dynamic = false) {
                Surface(Modifier.fillMaxSize()) {
                    MailDrawer(labels, MailView(), null, emptyMap(), "me@example.com", 0, { opened = it }, {}, {},
                        accounts = listOf(home, work), accountCounts = counts,
                        accountPins = DrawerAccountPins(AccountFolderPrefs(moreOpen = setOf(2L)), create = { a, name ->
                            refuse?.let { throw ApiException(400, it) }
                            made += "$a $name"
                            Label(30, name, homeAccountId = a)
                        }))
                }
            }
        }
    }

    @Test fun theHeadingsMenuMakesAFolderThatOpens() {
        drawer()
        compose.onNode(hasScrollAction()).performScrollToNode(hasTestTag("drawer-account-menu-2"))
        compose.onNodeWithTag("drawer-account-menu-2").performClick()
        compose.onNodeWithText("New folder").performClick()
        compose.onNodeWithText("New folder in work@example.org").assertExists()
        // An existing name is refused before anything is sent.
        compose.onNodeWithTag("new-folder-name").performTextInput("travel")
        compose.onNodeWithText("A label with this name already exists.").assertExists()
        compose.onNodeWithTag("new-folder-create").assertIsNotEnabled()
        compose.onNodeWithTag("new-folder-name").performTextInput(" plans")
        compose.onNodeWithTag("new-folder-create").performClick()
        compose.until("opened != null") { opened != null }
        assertEquals(listOf("2 travel plans"), made)
        assertEquals("acct:2:label:30", opened?.view)
        assertEquals("travel plans · work@example.org", opened?.title)
        compose.onAllNodes(isDialog()).assertCountEquals(0)
    }

    @Test fun aLongPressOnAFolderMakesOneInsideIt() {
        drawer()
        compose.onNode(hasScrollAction()).performScrollToNode(hasTestTag("drawer-more-2"))
        // Travel in Work's group (under More folders, open); the unified Labels list has it too.
        compose.onAllNodesWithText("Travel")[0].performTouchInput { longClick() }
        compose.onNodeWithText("New folder inside").performClick()
        compose.onNode(hasText("Inside") and hasText("Travel")).assertExists() // the parent the dialog starts with
        compose.onNodeWithTag("new-folder-name").performTextInput("2027")
        compose.onNodeWithTag("new-folder-create").performClick()
        compose.until("opened != null") { opened != null }
        assertEquals(listOf("2 Travel/2027"), made)
    }

    @Test fun aRefusalStaysInTheDialog() {
        refuse = "The name is too long"
        drawer()
        compose.onNode(hasScrollAction()).performScrollToNode(hasTestTag("drawer-account-menu-1"))
        compose.onNodeWithTag("drawer-account-menu-1").performClick()
        compose.onNodeWithText("New folder").performClick()
        compose.onNodeWithTag("new-folder-name").performTextInput("Receipts")
        compose.onNodeWithTag("new-folder-create").performClick()
        compose.until("\"Couldn't create the folder. The name is too long\" shown") { compose.onAllNodesWithText("Couldn't create the folder. The name is too long").fetchSemanticsNodes().isNotEmpty() }
        assertEquals(null, opened)
        assertTrue(made.isEmpty())
    }

    // A phone's height: the capture is the whole window behind the dialog.
    @Config(qualifiers = "w411dp-h891dp-xxhdpi")
    @Test fun dialogLight() = shot(false)

    @Config(qualifiers = "w411dp-h891dp-xxhdpi")
    @Test fun dialogDark() = shot(true)

    private fun shot(dark: Boolean) {
        compose.setContent {
            MailrTheme(if (dark) ThemeMode.DARK else ThemeMode.LIGHT, dynamic = false) {
                Surface(Modifier.fillMaxSize()) {
                    NewFolderDialog("work@example.org", labels, "Travel", onDismiss = {}) { }
                }
            }
        }
        compose.onNodeWithTag("new-folder-name").performTextInput("2027")
        compose.waitForIdle()
        compose.onNode(isDialog()).captureRoboImage("screenshots/new-folder-dialog-${if (dark) "dark" else "light"}.png")
    }

    // ---------- the request (POST /labels) ----------

    private lateinit var server: MockWebServer
    private lateinit var db: MailrDb
    private lateinit var repo: MailRepository
    private val requests = CopyOnWriteArrayList<Pair<RecordedRequest, String>>()

    @Before fun setUp() {
        server = MockWebServer()
        server.dispatcher = object : Dispatcher() {
            override fun dispatch(request: RecordedRequest): MockResponse {
                val body = request.body.readUtf8()
                requests += request to body
                val made = requests.any { it.first.method == "POST" }
                return when (request.path.orEmpty()) {
                    "/api/v1/labels" -> if (request.method == "POST") MockResponse().setBody(
                        """{"id":30,"name":"Receipts","home_account_id":2,"unread":0,"total":0}""")
                    else MockResponse().setBody(
                        """[{"id":1,"name":"Inbox","role":"inbox"}${if (made) ""","{"id":30,"name":"Receipts","home_account_id":2}""" else ""}]""")
                    "/api/v1/me" -> MockResponse().setBody(
                        """{"user":{"id":1,"email":"me@example.com"},"settings":{${if (made) """"account_folder_pins":{"2":[30]}""" else ""}}}""")
                    "/api/v1/inbox/count" -> MockResponse().setBody("""{"count":0}""")
                    else -> MockResponse().setResponseCode(404)
                }
            }
        }
        server.start()
        val ctx = ApplicationProvider.getApplicationContext<Application>()
        val sessions = SessionStore(ctx).apply {
            server = this@NewFolderTest.server.url("/").toString()
            save("tok-123", "csrf-456", "me@example.com", "Me")
        }
        db = MailrDb.create(ctx, inMemory = true)
        repo = MailRepository(MailrApi(ApiClient(sessions)), db)
    }

    @After fun tearDown() {
        db.close()
        server.shutdown()
    }

    @Test fun theFolderIsALabelWithItsAccountAsHome() = runTest {
        repo.refreshMe()
        val lb = repo.createFolder("Receipts", 2)
        assertEquals(30L, lb.id)
        val (req, body) = requests.first { it.first.method == "POST" }
        assertEquals("/api/v1/labels", req.path)
        val sent = Json.parseToJsonElement(body).jsonObject
        assertEquals("Receipts", sent["name"]!!.jsonPrimitive.content)
        assertEquals(2L, sent["home_account_id"]!!.jsonPrimitive.long)
        // The labels and the pins are read again: the folder shows pinned in its group at once.
        assertEquals(2L, repo.labels.value.first { it.id == 30L }.homeAccountId)
        assertEquals(listOf(30L), accountFolderPrefs(repo.me.value?.settings).pinsOf(2))
    }
}
