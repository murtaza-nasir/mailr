// ACT-SURFACES: android.share
package io.github.murtazanasir.mailr

import android.app.Application
import android.os.Looper
import androidx.test.core.app.ApplicationProvider
import io.github.murtazanasir.mailr.data.api.ApiClient
import io.github.murtazanasir.mailr.data.api.Label
import io.github.murtazanasir.mailr.data.api.MailrApi
import io.github.murtazanasir.mailr.data.api.MailrJson
import io.github.murtazanasir.mailr.data.api.SendRequest
import io.github.murtazanasir.mailr.data.auth.SessionStore
import io.github.murtazanasir.mailr.data.db.MailrDb
import io.github.murtazanasir.mailr.data.share.FileMeta
import io.github.murtazanasir.mailr.data.share.SelfSendRepository
import io.github.murtazanasir.mailr.data.share.SelfState
import io.github.murtazanasir.mailr.data.share.SharedContent
import io.github.murtazanasir.mailr.testing.eventually
import io.github.murtazanasir.mailr.ui.share.SelfLabels
import io.github.murtazanasir.mailr.ui.share.SelfPhase
import io.github.murtazanasir.mailr.ui.share.SelfPreview
import io.github.murtazanasir.mailr.ui.share.ShareToSelfViewModel
import kotlinx.coroutines.flow.first
import kotlinx.coroutines.runBlocking
import kotlinx.serialization.encodeToString
import kotlinx.serialization.json.jsonObject
import okhttp3.mockwebserver.Dispatcher
import okhttp3.mockwebserver.MockResponse
import okhttp3.mockwebserver.MockWebServer
import okhttp3.mockwebserver.RecordedRequest
import okhttp3.mockwebserver.SocketPolicy
import org.junit.After
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
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
 * Labels on the Send to self card (mail.send_to_self_labels, Android 0.5.9): the chips' order,
 * the labels of the last send picked again, the Send tap carrying them in POST /send
 * (`label_ids`), and a share queued offline keeping them until it goes. Also the card's preview
 * of what is sent (mail.send_to_self).
 */
@RunWith(RobolectricTestRunner::class)
@Config(application = Application::class, sdk = [34])
class SelfShareLabelsTest {
    @get:Rule(order = Int.MIN_VALUE) val graphs = TestGraph()
    private lateinit var server: MockWebServer
    private val requests = CopyOnWriteArrayList<RecordedRequest>()
    @Volatile private var offline = false

    private val me = """{"user":{"id":1,"email":"me@example.com","name":"Me"},
        "identities":[{"id":9,"email":"me@example.com","name":"Me","is_default":true}]}"""

    private val labels = listOf(
        Label(1, "Inbox", "inbox", total = 400), Label(4, "Trash", "trash", total = 900),
        Label(10, "Recipes", color = "#e37400", total = 12),
        Label(11, "Reading", color = "#f9ab00", total = 80),
        Label(12, "Later/Watch", color = "#1a73e8", total = 3),
        Label(13, "Travel", color = "#a142f4", total = 40),
        Label(14, "Finance", color = "#34a853", total = 60),
        Label(15, "Family", color = "#e91e63", total = 5),
        Label(16, "Studio", color = "#8e24aa", total = 1),
    )

    @Before fun setUp() {
        server = MockWebServer()
        server.dispatcher = object : Dispatcher() {
            override fun peek(): MockResponse =
                if (offline) MockResponse().setSocketPolicy(SocketPolicy.DISCONNECT_AT_START) else super.peek()

            override fun dispatch(request: RecordedRequest): MockResponse {
                if (offline) return MockResponse().setSocketPolicy(SocketPolicy.DISCONNECT_AFTER_REQUEST)
                requests += request
                return when (request.path.orEmpty()) {
                    "/api/v1/me" -> MockResponse().setBody(me)
                    "/api/v1/labels" -> MockResponse().setBody(MailrJson.encodeToString(labels))
                    "/api/v1/send" -> MockResponse().setBody("""{"outbox":{"id":41,"status":"queued","send_at":"2030-01-01T10:00:10+00:00"}}""")
                    else -> MockResponse().setResponseCode(404).setBody("""{"detail":"not here"}""")
                }
            }
        }
        server.start()
        graphs.closeLast(AutoCloseable { server.shutdown() })
        SessionStore(ApplicationProvider.getApplicationContext<Application>()).apply {
            server = this@SelfShareLabelsTest.server.url("/").toString()
            save("tok-123", "csrf-456", "me@example.com", "Me")
        }
    }

    @After fun tearDown() {
        idle()
    }

    private fun idle() = shadowOf(Looper.getMainLooper()).idle()

    private fun sends() = requests.filter { it.path == "/api/v1/send" }.map {
        MailrJson.decodeFromString(SendRequest.serializer(), it.body.clone().readUtf8())
    }

    private fun lastSendJson() = MailrJson.parseToJsonElement(
        requests.last { it.path == "/api/v1/send" }.body.clone().readUtf8()).jsonObject["payload"]!!.jsonObject

    private val resolver get() = ApplicationProvider.getApplicationContext<Application>().contentResolver

    private fun graphWith(last: List<Long>, recent: List<Long> = last): AppGraph {
        val g = graphs.graph()
        runBlocking { g.settings.update { it.copy(selfSendLabels = last, selfSendRecentLabels = recent) } }
        return g
    }

    private fun card(g: AppGraph, shared: SharedContent = SharedContent(subject = "Lentil soup", text = "https://example.com/soup")) =
        graphs.vm { ShareToSelfViewModel(g, shared, resolver) }.also { vm ->
            eventually("the card is ready with labels") { vm.state.value.phase == SelfPhase.CONFIRM && vm.state.value.labels.isNotEmpty() }
        }

    // ---------------- the chips ----------------

    @Test fun quickChipsPutPickedThenRecentThenMostUsed() {
        val q = SelfLabels.quick(labels, recent = listOf(15, 12), selected = listOf(12))
        assertEquals(listOf(12L, 15L, 11L, 14L, 13L), q.map { it.id })
        // System labels are never chips, however busy.
        assertTrue(q.none { it.role != null })
        // A picked label past the limit still shows.
        assertEquals(7, SelfLabels.quick(labels, emptyList(), labels.filter { it.role == null }.map { it.id }).size)
    }

    @Test fun previewShowsLinkTextImagesAndFilesWithSizes() {
        val link = SelfPreview.of(SharedContent(subject = "Lentil soup", text = "https://www.example.com/soup"), emptyList())
        assertEquals("example.com", link.link!!.domain)
        assertEquals("Lentil soup", link.link!!.title)
        assertNull(link.text)
        val mixed = SelfPreview.of(SharedContent(text = "Look at this: https://x.org/a"), listOf(
            FileMeta("IMG_1.jpg", "image/jpeg", 2_400_000), FileMeta("plan.pdf", "application/pdf", 120_000),
            FileMeta("IMG_2.png", "image/png", 800_000)), listOf("content://a/1", "content://a/2", "content://a/3"))
        assertEquals("Look at this: https://x.org/a", mixed.text)
        assertNull(mixed.link!!.title)
        assertEquals(listOf("content://a/1", "content://a/3"), mixed.images.map { it.model })
        assertEquals(listOf("plan.pdf"), mixed.files.map { it.name })
        assertEquals(120_000L, mixed.files.single().size)
    }

    // ---------------- the card ----------------

    @Test fun theLabelsOfTheLastSendArePickedAgain() {
        val vm = card(graphWith(last = listOf(12L, 15L)))
        assertEquals(listOf(12L, 15L), vm.state.value.selected)
        assertEquals(listOf(12L, 15L), vm.state.value.quickLabels.take(2).map { it.id })
    }

    @Test fun neverUsedPicksNothing() {
        val vm = card(graphWith(last = emptyList()))
        assertTrue(vm.state.value.selected.isEmpty())
        // The busiest labels lead the chips.
        assertEquals(listOf(11L, 14L, 13L), vm.state.value.quickLabels.take(3).map { it.id })
    }

    @Test fun aLabelDeletedSinceIsNotPicked() {
        val vm = card(graphWith(last = listOf(99L, 13L)))
        assertEquals(listOf(13L), vm.state.value.selected)
    }

    @Test fun togglingAndThePickerChangeTheChoice() {
        val vm = card(graphWith(last = listOf(12L)))
        vm.toggleLabel(13)
        vm.toggleLabel(12)
        assertEquals(listOf(13L), vm.state.value.selected)
        val before = vm.state.value.quickLabels.map { it.id }
        assertFalse(16L in before)
        // "More…": Studio is picked in the full picker and joins the chips at the front.
        vm.pickLabels(listOf(13L, 16L))
        assertEquals(listOf(13L, 16L), vm.state.value.selected)
        assertEquals(16L, vm.state.value.quickLabels.first().id)
    }

    @Test fun sendCarriesTheLabelIdsAndRemembersThem() {
        val g = graphWith(last = listOf(12L))
        val vm = card(g)
        vm.toggleLabel(10)
        vm.setSubject("Soup for Sunday")
        vm.setNote("Double the cumin")
        vm.confirm()
        val row = runBlocking { g.db.selfSends().all().single() }
        eventually("queued") { runBlocking { g.selfSend.get(row.id)?.state } == SelfState.QUEUED }
        assertEquals(SelfSendRepository.Outcome.DONE, runBlocking { g.selfSend.process(row.id) })
        val p = sends().single().payload
        assertEquals(listOf(12L, 10L), p.labelIds)
        assertEquals("Soup for Sunday", p.subject)
        assertTrue(p.text!!.startsWith("Double the cumin"))
        assertEquals("android-share", p.headers["X-Mailr-Origin"])
        // The next card starts with these.
        assertEquals(listOf(12L, 10L), runBlocking { g.settings.prefs.first().selfSendLabels })
        assertEquals(listOf(12L, 10L), runBlocking { g.settings.prefs.first().selfSendRecentLabels }.take(2))
    }

    @Test fun noLabelsSendsNoLabelKey() {
        val g = graphWith(last = emptyList())
        val vm = card(g)
        vm.confirm()
        val row = runBlocking { g.db.selfSends().all().single() }
        eventually("queued") { runBlocking { g.selfSend.get(row.id)?.state } == SelfState.QUEUED }
        runBlocking { g.selfSend.process(row.id) }
        assertFalse("label_ids" in lastSendJson())
        assertTrue(runBlocking { g.settings.prefs.first().selfSendLabels }.isEmpty())
    }

    // ---------------- the offline queue ----------------

    @Test fun offlineReplayKeepsTheLabels() = runBlocking {
        val ctx = ApplicationProvider.getApplicationContext<Application>()
        val db = MailrDb.create(ctx, inMemory = true)
        try {
            val repo = SelfSendRepository(ctx.filesDir, MailrApi(ApiClient(SessionStore(ctx))), db.selfSends(), titleOf = { null })
            val id = repo.create(SharedContent(subject = "Flight", text = "https://example.com/flight"), resolver)
            assertTrue(repo.confirm(id, labelIds = listOf(13L, 15L)))
            offline = true
            assertEquals(SelfSendRepository.Outcome.RETRY, repo.process(id))
            assertEquals(SelfState.QUEUED, repo.get(id)!!.state)
            assertEquals("13,15", repo.get(id)!!.labelIds)
            offline = false
            assertEquals(SelfSendRepository.Outcome.DONE, repo.process(id))
            assertEquals(listOf(13L, 15L), sends().single().payload.labelIds)
            // A second tap on an already queued share changes nothing.
            assertFalse(repo.confirm(id, labelIds = listOf(10L)))
        } finally {
            db.close()
        }
    }

    @Test fun aNoteAfterSendingKeepsTheLabels() = runBlocking {
        val ctx = ApplicationProvider.getApplicationContext<Application>()
        val db = MailrDb.create(ctx, inMemory = true)
        try {
            val repo = SelfSendRepository(ctx.filesDir, MailrApi(ApiClient(SessionStore(ctx))), db.selfSends(), titleOf = { null })
            val id = repo.create(SharedContent(subject = "S", text = "https://x.org"), resolver)
            repo.confirm(id, labelIds = listOf(11L))
            repo.process(id)
            requests.clear()
            db.selfSends().transition(id, SelfState.HELD, SelfState.EDITING)
            repo.sendWithNote(id, "read later")
            repo.process(id)
            assertEquals(listOf(11L), sends().single().payload.labelIds)
        } finally {
            db.close()
        }
    }
}
