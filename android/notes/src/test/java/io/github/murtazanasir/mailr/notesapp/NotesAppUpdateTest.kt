package io.github.murtazanasir.mailr.notesapp

import android.app.Application
import android.content.Intent
import androidx.compose.ui.test.hasText
import androidx.compose.ui.test.onNodeWithTag
import androidx.compose.ui.test.onNodeWithText
import androidx.compose.ui.test.performClick
import androidx.test.core.app.ApplicationProvider
import io.github.murtazanasir.mailr.data.auth.SessionStore
import io.github.murtazanasir.mailr.notes.FakeNotesServer
import io.github.murtazanasir.mailr.notes.data.NotesRepository.DrainResult
import io.github.murtazanasir.mailr.notes.data.OpState
import io.github.murtazanasir.mailr.notesapp.ui.NotesAppRoot
import io.github.murtazanasir.mailr.notesapp.ui.SettingsScreen
import io.github.murtazanasir.mailr.testing.createComposeRule
import io.github.murtazanasir.mailr.testing.eventually
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.first
import kotlinx.coroutines.runBlocking
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

/**
 * The Notes app and the server's oldest-version floor (426) and release list
 * (GET /api/v1/app/releases): a 426 pauses the replay queue with every write kept, the banner
 * "Update mailr Notes to keep syncing" shows over the app and Update opens /api/v1/app/notes.apk,
 * and Settings offers a newer version code and nothing otherwise. The Notes app's own floor
 * (settings.notes_min_version) reads this app's User-Agent.
 */
@RunWith(RobolectricTestRunner::class)
@Config(application = Application::class, sdk = [34], qualifiers = "w411dp-h891dp")
class NotesAppUpdateTest {
    @get:Rule(order = Int.MIN_VALUE) val graphs = NotesTestGraph()
    @get:Rule val compose = createComposeRule()
    private lateinit var fake: FakeNotesServer
    private lateinit var g: NotesGraph
    private val ctx get() = ApplicationProvider.getApplicationContext<Application>()
    private val code = BuildConfig.VERSION_CODE.toLong()

    @Before fun setUp() {
        fake = graphs.closeLast(object : AutoCloseable {
            val f = FakeNotesServer().start()
            override fun close() = f.close()
        }).f
        SessionStore(ctx).apply {
            server = fake.server.url("/").toString().trimEnd('/')
            save("tok-1", "csrf-1", "me@example.com", "Me")
        }
        g = graphs.graph(ctx)
    }

    private fun releaseList(notesCode: Long?, pkg: String = ctx.packageName) =
        """{"apps":[{"app":"mailr","name":"mailr","package":"io.github.murtazanasir.mailr","url":"https://example.com/mailr.apk",
            "on_server":true,"version_name":"99.0","version_code":999999},
            {"app":"notes","name":"mailr Notes","package":"$pkg","url":"${fake.server.url("/api/v1/app/notes.apk")}",
            "on_server":true,"version_name":"9.9","version_code":${notesCode ?: "null"}}]}"""

    @Test fun a426PausesTheQueueAndKeepsEveryWrite() = runBlocking {
        val repo = g.notes.repo
        repo.createLabel("Home")
        repo.createLabel("Work")
        fake.tooOld = true
        assertEquals(DrainResult.PAUSED, repo.drain())
        assertTrue(g.updates.required.value)
        val ops = repo.ops.first()
        assertEquals(2, ops.size)
        assertTrue(ops.all { it.state == OpState.QUEUED && it.attempts == 0 })
        assertEquals(1, fake.log.count { it.method == "POST" && it.path == "notes/labels" })
        // The server takes the app again: the first write that goes through clears it, and both go.
        fake.tooOld = false
        assertEquals(DrainResult.DONE, repo.drain())
        assertFalse(g.updates.required.value)
        assertTrue(repo.ops.first().isEmpty())
        assertEquals(3, fake.log.count { it.method == "POST" && it.path == "notes/labels" })
    }

    /** "1.1.0-debug" → "1.1.1": one patch above this build. */
    private fun above(v: String): String {
        val p = v.substringBefore('-').split('.').map { it.toInt() } + listOf(0, 0, 0)
        return "${p[0]}.${p[1]}.${p[2] + 1}"
    }

    // settings.notes_min_version: the server's floor for the Notes app reads this app's User-Agent.
    @Test fun theNotesFloorReadsThisAppsUserAgentAndPausesTheQueueBelowIt() = runBlocking {
        val repo = g.notes.repo
        // A floor at this version answers it.
        fake.notesFloor = BuildConfig.VERSION_NAME.substringBefore('-')
        repo.createLabel("Home")
        assertEquals(DrainResult.DONE, repo.drain())
        val ua = fake.log.last { it.path == "notes/labels" }.userAgent.orEmpty()
        assertTrue(ua, Regex("""^mailr-android-notes/\d+\.\d+(\.\d+)?""").containsMatchIn(ua))
        assertFalse(g.updates.required.value)
        // One patch above it: 426, every write waits, and the update is asked for.
        fake.notesFloor = above(BuildConfig.VERSION_NAME)
        repo.createLabel("Work")
        assertEquals(DrainResult.PAUSED, repo.drain())
        assertTrue(g.updates.required.value)
        assertEquals(1, repo.ops.first().size)
        // The admin lowers it again: the write goes and the banner leaves.
        fake.notesFloor = ""
        assertEquals(DrainResult.DONE, repo.drain())
        assertFalse(g.updates.required.value)
        assertTrue(repo.ops.first().isEmpty())
    }

    @Test fun theBannerShowsOverTheAppAndUpdateOpensTheDownload() {
        fake.note("text", "Plan")
        fake.tooOld = true
        compose.setContent { NotesAppRoot(g, MutableStateFlow(null)) {} }
        compose.eventually("the banner") { compose.onAllNodes(hasText("Update Mailr Notes to keep syncing")).fetchSemanticsNodes().isNotEmpty() }
        compose.onNodeWithTag("update-required-action").performClick()
        val started = shadowOf(ctx).nextStartedActivity
        assertNotNull(started)
        assertEquals(Intent.ACTION_VIEW, started.action)
        assertEquals(fake.server.url("/api/v1/app/notes.apk").toString(), started.dataString)
        // The floor is lowered: the next sync goes through and the banner leaves.
        fake.tooOld = false
        runBlocking { g.notes.repo.pull() }
        compose.eventually("the banner gone") { compose.onAllNodes(hasText("Update Mailr Notes to keep syncing")).fetchSemanticsNodes().isEmpty() }
    }

    @Test fun settingsOffersANewerVersionCodeOnly() {
        fake.releases = releaseList(code + 1)
        assertEquals(code + 1, runBlocking { g.updates.check() }?.versionCode)
        val session = g.sessions.session.value!!
        compose.setContent { SettingsScreen(g, session, onBack = {}) }
        compose.onNodeWithText("Update available").assertExists()
        compose.onNodeWithText("Mailr Notes 9.9 is ready to download").assertExists()
        compose.onNodeWithTag("update-available").performClick()
        assertEquals(fake.server.url("/api/v1/app/notes.apk").toString(), shadowOf(ctx).nextStartedActivity.dataString)
        // The same version, or none on the server: no offer, and the row leaves.
        for (c in listOf(code, null)) {
            fake.releases = releaseList(c)
            assertNull(runBlocking { g.updates.check(force = true) })
        }
        compose.waitForIdle()
        compose.onNodeWithText("Update available").assertDoesNotExist()
        compose.onNodeWithText("Account").assertExists()
    }
}
