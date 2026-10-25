package io.github.murtazanasir.mailr

import android.app.Application
import android.content.Intent
import androidx.compose.material3.Text
import androidx.compose.runtime.collectAsState
import androidx.compose.ui.test.onNodeWithTag
import androidx.compose.ui.test.onNodeWithText
import androidx.compose.ui.test.performClick
import androidx.test.core.app.ApplicationProvider
import io.github.murtazanasir.mailr.data.api.ApiClient
import io.github.murtazanasir.mailr.data.auth.SessionStore
import io.github.murtazanasir.mailr.data.db.PendingState
import io.github.murtazanasir.mailr.data.repo.MailRepository.DrainResult
import io.github.murtazanasir.mailr.data.repo.PendingScopes
import io.github.murtazanasir.mailr.data.settings.Prefs
import io.github.murtazanasir.mailr.data.update.AppUpdates
import io.github.murtazanasir.mailr.testing.createComposeRule
import io.github.murtazanasir.mailr.testing.until
import io.github.murtazanasir.mailr.ui.settings.SettingsCallbacks
import io.github.murtazanasir.mailr.ui.settings.SettingsScreen
import io.github.murtazanasir.mailr.ui.update.UpdateRequiredFrame
import kotlinx.coroutines.runBlocking
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
import org.junit.Before
import org.junit.Rule
import org.junit.Test
import org.junit.runner.RunWith
import org.robolectric.RobolectricTestRunner
import org.robolectric.Shadows.shadowOf
import org.robolectric.annotation.Config
import java.util.concurrent.CopyOnWriteArrayList

/**
 * The server's oldest-version floor (426, core/appversion.py) and the release list
 * (GET /api/v1/app/releases): a 426 pauses the replay queue with every change kept, shows the
 * update banner, and clears when a later call goes through or a newer build starts; the daily check
 * offers a newer version code in Settings and nothing otherwise.
 */
@RunWith(RobolectricTestRunner::class)
@Config(application = Application::class, sdk = [34])
class AppUpdateTest {
    @get:Rule(order = Int.MIN_VALUE) val graphs = TestGraph()
    @get:Rule val compose = createComposeRule()

    private lateinit var server: MockWebServer
    private lateinit var g: AppGraph
    private val ctx get() = ApplicationProvider.getApplicationContext<Application>()
    private val code = BuildConfig.VERSION_CODE.toLong()
    private val paths = CopyOnWriteArrayList<String>()
    private val sent = CopyOnWriteArrayList<String>()
    @Volatile private var tooOld = false
    @Volatile private var releases: String? = null

    @Before fun setUp() {
        server = MockWebServer()
        server.dispatcher = object : Dispatcher() {
            override fun dispatch(request: RecordedRequest): MockResponse {
                val path = request.path.orEmpty()
                paths += path
                if (path == "/api/v1/app/releases") {
                    return releases?.let { MockResponse().setHeader("Content-Type", "application/json").setBody(it) }
                        ?: MockResponse().setResponseCode(404).setBody("""{"detail":"not here"}""")
                }
                if (tooOld) return MockResponse().setResponseCode(426)
                    .setBody("""{"detail":"This version of the Mailr app is no longer supported. Update it to 9.0.0 or later."}""")
                if (path == "/api/v1/conversations/actions") {
                    sent += request.body.readUtf8()
                    return MockResponse().setBody("""{"ok":true,"affected":1}""")
                }
                // Any other call goes through (what it answers does not matter here).
                return MockResponse().setBody("{}")
            }
        }
        server.start()
        SessionStore(ctx).apply {
            server = this@AppUpdateTest.server.url("/").toString()
            save("tok-123", "csrf-456", "me@example.com", "Me")
        }
        g = graphs.graph(ctx)
    }

    @After fun tearDown() {
        graphs.close()
        server.shutdown()
    }

    private fun queue(action: String, id: Long) = runBlocking {
        g.mail.enqueue("POST", "conversations/actions", """{"ids":[$id],"action":"$action"}""", "k-$action-$id",
            PendingScopes.describe(action, 1))
    }

    private fun ops() = runBlocking { g.db.pending().next(50) }

    private fun releaseList(mailrCode: Long?, pkg: String = ctx.packageName, name: String = "9.9.9") =
        """{"apps":[{"app":"mailr","name":"mailr","package":"$pkg","url":"${server.url("/api/v1/app/mailr.apk")}",
            "on_server":true,"version_name":"$name","version_code":${mailrCode ?: "null"},"size":1,"sha256":"x","published_at":null},
            {"app":"notes","name":"mailr Notes","package":"io.github.murtazanasir.mailr.notes","url":"https://example.com/notes.apk",
            "on_server":true,"version_name":"99.0","version_code":999999,"size":1,"sha256":"y","published_at":null}]}"""

    @Test fun a426PausesTheQueueKeepsEveryChangeAndResumesWhenTheServerTakesTheAppAgain() = runBlocking {
        queue("archive", 1)
        queue("star", 2)
        tooOld = true
        assertEquals(DrainResult.PAUSED, g.mail.drain())
        assertTrue(g.updates.required.value)
        // Nothing turned FAILED, nothing counted an attempt, and the queue is not waiting for sign-in.
        assertEquals(2, ops().size)
        assertTrue(ops().all { it.state == PendingState.QUEUED && it.attempts == 0 })
        assertFalse(g.mail.queuePaused.value)
        // Each pass tries the first change once and stops.
        assertEquals(DrainResult.PAUSED, g.mail.drain())
        assertEquals(2, paths.count { it == "/api/v1/conversations/actions" })
        // The floor is lowered (or the app updated): the first call that goes through clears it and both go, in order.
        var resumed = 0
        g.updates.onResumed = { resumed++ }
        tooOld = false
        assertEquals(DrainResult.DONE, g.mail.drain())
        assertFalse(g.updates.required.value)
        assertEquals(1, resumed)
        assertEquals(2, sent.size)
        assertTrue(sent[0].contains("archive") && sent[1].contains("star"))
        assertTrue(ops().isEmpty())
    }

    @Test fun theRefusalIsKeptForThisBuildAndClearsWithANewerOne() {
        g.updates.upgradeRequired()
        // The app restarts at the same version: still refused.
        assertTrue(AppUpdates(ctx, ApiClient(SessionStore(ctx)), "mailr", "mailr", code).required.value)
        // A newer build starts: the refusal was for the old one.
        assertFalse(AppUpdates(ctx, ApiClient(SessionStore(ctx)), "mailr", "mailr", code + 1).required.value)
        assertFalse(AppUpdates(ctx, ApiClient(SessionStore(ctx)), "mailr", "mailr", code).required.value)
    }

    @Test fun theBannerShowsOnA426AndUpdateOpensTheDownload() {
        compose.setContent { UpdateRequiredFrame(g.updates) { Text("Inbox") } }
        compose.onNodeWithText("Inbox").assertExists()
        compose.onNodeWithTag("update-required").assertDoesNotExist()
        tooOld = true
        runBlocking { runCatching { g.mail.refreshLabels() } }
        compose.until("g.updates.required.value") { g.updates.required.value }
        compose.onNodeWithText("Update Mailr to keep syncing").assertExists()
        compose.onNodeWithText("Inbox").assertExists()
        compose.onNodeWithTag("update-required-action").performClick()
        val started = shadowOf(ctx).nextStartedActivity
        assertNotNull(started)
        assertEquals(Intent.ACTION_VIEW, started.action)
        assertEquals(server.url("/api/v1/app/mailr.apk").toString(), started.dataString)
        // A later call that goes through: the banner leaves.
        tooOld = false
        runBlocking { runCatching { g.mail.refreshLabels() } }
        compose.until("!g.updates.required.value") { !g.updates.required.value }
        compose.onNodeWithTag("update-required").assertDoesNotExist()
    }

    @Test fun theCheckOffersANewerVersionCodeInSettings() {
        releases = releaseList(code + 1)
        val offer = runBlocking { g.updates.check() }
        assertNotNull(offer)
        assertEquals(code + 1, offer!!.versionCode)
        compose.setContent {
            val available = g.updates.available.collectAsStateValue()
            SettingsScreen(Prefs(), "Me", "me@example.com", server.url("/").toString(), emptyList(),
                SettingsCallbacks(back = {}, update = {}, revoke = {}, signOut = {}, openWeb = {},
                    updateAvailable = available, openUpdate = { g.updates.open(ctx) }))
        }
        compose.onNodeWithText("Update available").assertExists()
        compose.onNodeWithText("Mailr 9.9.9 is ready to download").assertExists()
        compose.onNodeWithTag("update-available").performClick()
        assertEquals(server.url("/api/v1/app/mailr.apk").toString(), shadowOf(ctx).nextStartedActivity.dataString)
        // Kept for the next start of the app.
        assertEquals(code + 1, AppUpdates(ctx, ApiClient(SessionStore(ctx)), "mailr", "mailr", code).available.value?.versionCode)
    }

    @Test fun theSameOrAnOlderVersionCodeOffersNothing() {
        for (c in listOf(code, code - 1, null)) {
            releases = releaseList(c)
            assertNull(runBlocking { g.updates.check(force = true) })
            assertNull(g.updates.available.value)
        }
        // Another package (a release for a debug build, or another app) is not offered either.
        releases = releaseList(code + 5, pkg = "org.example.other")
        assertNull(runBlocking { g.updates.check(force = true) })
        compose.setContent {
            SettingsScreen(Prefs(), "Me", "me@example.com", server.url("/").toString(), emptyList(),
                SettingsCallbacks(back = {}, update = {}, revoke = {}, signOut = {}, openWeb = {},
                    updateAvailable = g.updates.available.collectAsStateValue()))
        }
        compose.onNodeWithText("Look").assertExists()
        compose.onNodeWithText("Update available").assertDoesNotExist()
    }

    @Test fun theCheckRunsAtMostOnceADayAndAFailureKeepsWhatWasKnown() = runBlocking {
        var now = 1_000_000_000L
        val u = AppUpdates(ctx, ApiClient(SessionStore(ctx)), "mailr", "mailr", code, ctx.packageName, clock = { now })
        releases = releaseList(code + 1)
        assertNotNull(u.check())
        val asked = paths.count { it == "/api/v1/app/releases" }
        releases = releaseList(code)
        now += AppUpdates.CHECK_EVERY - 1
        assertNotNull(u.check())     // within the day: not asked again
        assertEquals(asked, paths.count { it == "/api/v1/app/releases" })
        releases = null               // the list fails (404): what was known stays
        now += 2
        assertNotNull(u.check())
        releases = releaseList(code)  // the server's version is no longer newer
        assertNull(u.check(force = true))
    }
}

@androidx.compose.runtime.Composable
private fun <T> kotlinx.coroutines.flow.StateFlow<T>.collectAsStateValue(): T = collectAsState().value
