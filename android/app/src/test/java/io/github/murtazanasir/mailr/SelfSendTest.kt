package io.github.murtazanasir.mailr

import android.app.Application
import android.content.ClipData
import android.content.Intent
import android.net.Uri
import androidx.test.core.app.ApplicationProvider
import io.github.murtazanasir.mailr.data.api.ApiClient
import io.github.murtazanasir.mailr.data.api.MailrApi
import io.github.murtazanasir.mailr.data.api.MailrJson
import io.github.murtazanasir.mailr.data.api.SendRequest
import io.github.murtazanasir.mailr.data.auth.SessionStore
import io.github.murtazanasir.mailr.data.db.MailrDb
import io.github.murtazanasir.mailr.data.share.SelfSendRepository
import io.github.murtazanasir.mailr.data.share.SelfState
import io.github.murtazanasir.mailr.data.share.SelfSubject
import io.github.murtazanasir.mailr.data.share.ShareParser
import io.github.murtazanasir.mailr.data.share.SharedContent
import io.github.murtazanasir.mailr.data.share.UndoResult
import io.github.murtazanasir.mailr.ui.share.ShareComposeActivity
import io.github.murtazanasir.mailr.ui.util.Format
import kotlinx.coroutines.test.runTest
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
import org.junit.Before
import org.junit.Test
import org.junit.runner.RunWith
import org.robolectric.RobolectricTestRunner
import org.robolectric.annotation.Config
import java.util.concurrent.CopyOnWriteArrayList

@RunWith(RobolectricTestRunner::class)
@Config(application = Application::class, sdk = [34])
class SelfSendTest {
    private lateinit var server: MockWebServer
    private lateinit var db: MailrDb
    private lateinit var repo: SelfSendRepository
    private val requests = CopyOnWriteArrayList<RecordedRequest>()
    private val enqueued = CopyOnWriteArrayList<String>()
    @Volatile private var offline = false
    @Volatile private var cancelStatus = 200

    private val me = """{"user":{"id":1,"email":"login@example.com","name":"Me"},
        "identities":[{"id":7,"email":"other@example.com"},{"id":9,"email":"me@example.com","name":"Me","is_default":true}]}"""

    @Before fun setUp() {
        server = MockWebServer()
        server.dispatcher = object : Dispatcher() {
            override fun peek(): MockResponse =
                if (offline) MockResponse().setSocketPolicy(SocketPolicy.DISCONNECT_AT_START) else super.peek()

            override fun dispatch(request: RecordedRequest): MockResponse {
                if (offline) return MockResponse().setSocketPolicy(SocketPolicy.DISCONNECT_AT_START)
                requests += request
                val path = request.path.orEmpty()
                return when {
                    path == "/api/v1/me" -> MockResponse().setBody(me)
                    path == "/api/v1/uploads" -> MockResponse().setBody("""{"blob":"b${requests.size}","filename":"x","type":"image/png","size":3}""")
                    path == "/api/v1/send" -> MockResponse().setBody("""{"outbox":{"id":41,"status":"queued","send_at":"2030-01-01T10:00:10+00:00"}}""")
                    path == "/api/v1/outbox/41/cancel" -> MockResponse().setResponseCode(cancelStatus)
                        .setBody(if (cancelStatus == 200) """{"payload":null}""" else """{"detail":"already sent"}""")
                    else -> MockResponse().setResponseCode(404)
                }
            }
        }
        server.start()
        val ctx = ApplicationProvider.getApplicationContext<Application>()
        val sessions = SessionStore(ctx).apply {
            server = this@SelfSendTest.server.url("/").toString()
            save("tok-123", "csrf-456", "me@example.com", "Me")
        }
        db = MailrDb.create(ctx, inMemory = true)
        repo = SelfSendRepository(ctx.filesDir, MailrApi(ApiClient(sessions)), db.selfSends(),
            titleOf = { "Fetched Page Title" }).apply { enqueue = { enqueued += it } }
    }

    @After fun tearDown() {
        db.close()
        server.shutdown()
    }

    private fun sendBody(): SendRequest = MailrJson.decodeFromString(SendRequest.serializer(),
        requests.last { it.path == "/api/v1/send" }.body.readUtf8())

    // ---------------- subject rules ----------------

    @Test fun ownProviderUrisAreRefusedEvenWithAUserIdPrefix() {
        val own = "io.github.murtazanasir.mailr"
        assertFalse(ShareParser.safeUri(Uri.parse("content://io.github.murtazanasir.mailr.files/attachments/a.pdf"), own))
        assertFalse(ShareParser.safeUri(Uri.parse("content://0@io.github.murtazanasir.mailr.files/attachments/a.pdf"), own))
        assertFalse(ShareParser.safeUri(Uri.parse("file:///data/data/io.github.murtazanasir.mailr/x"), own))
        assertTrue(ShareParser.safeUri(Uri.parse("content://media/external/images/1"), own))
    }

    @Test fun subjectPrefersSharedSubjectThenTitleThenPageTitle() {
        assertEquals("Chrome title", SelfSubject.derive("Chrome title", "T", "Page", "https://a.b/c", emptyList()))
        assertEquals("T", SelfSubject.derive(null, "T", "Page", "https://a.b/c", emptyList()))
        assertEquals("Page", SelfSubject.derive(" ", null, "Page", "https://a.b/c", emptyList()))
    }

    @Test fun subjectFallsBackToFirstLineThenUrlThenFiles() {
        assertEquals("Great read", SelfSubject.derive(null, null, null, "Great read\nhttps://x.org/a", emptyList()))
        // "Title https://…" on one line: the URL is dropped from the subject.
        assertEquals("Look at this", SelfSubject.derive(null, null, null, "Look at this: https://x.org/a", emptyList()))
        assertEquals("example.com/story/42", SelfSubject.derive(null, null, null, "https://www.example.com/story/42/", emptyList()))
        assertEquals("a.jpg", SelfSubject.derive(null, null, null, null, listOf("a.jpg")))
        assertEquals("a.jpg and 2 more", SelfSubject.derive(null, null, null, null, listOf("a.jpg", "b.jpg", "c.pdf")))
        assertEquals("Buy milk", SelfSubject.derive(null, null, null, null, emptyList(), note = "Buy milk\nand eggs"))
        assertEquals(SelfSubject.FALLBACK, SelfSubject.derive(null, null, null, null, emptyList()))
        assertTrue(SelfSubject.derive("x".repeat(400), null, null, null, emptyList()).length <= 150)
    }

    @Test fun bodyPutsNoteFirstAndSkipsRepeatedSubject() {
        assertEquals("why\n\nhttps://a.b", SelfSubject.body("https://a.b", "why", "Title"))
        assertEquals("", SelfSubject.body("Title", null, "Title"))
    }

    @Test fun pageTitleParsing() {
        assertEquals("Tom & Jerry’s", SelfSubject.titleFromHtml("<html><head><title>\n Tom &amp; Jerry&#8217;s </title>"))
        assertEquals("OG wins", SelfSubject.titleFromHtml("""<meta property="og:title" content="OG wins"><title>t</title>"""))
        assertNull(SelfSubject.titleFromHtml("<html><body>no title</body></html>"))
        assertTrue(SelfSubject.isLoneUrl(" https://x.org/a?b=1 "))
        assertFalse(SelfSubject.isLoneUrl("see https://x.org"))
    }

    @Test fun fileNamesAreSanitised() {
        assertEquals("evil_.txt", SelfSubject.safeFileName("../../evil:.txt"))
        assertEquals("shared", SelfSubject.safeFileName("..."))
    }

    // ---------------- intent parsing ----------------

    @Test fun parsesTextSubjectAndStreamsFromClipData() {
        val own = "io.github.murtazanasir.mailr"
        val i = Intent(Intent.ACTION_SEND_MULTIPLE).apply {
            putExtra(Intent.EXTRA_SUBJECT, "  Subject ")
            putExtra(Intent.EXTRA_TEXT, "Hello")
            putParcelableArrayListExtra(Intent.EXTRA_STREAM, arrayListOf(Uri.parse("content://photos/1")))
            clipData = ClipData.newRawUri("", Uri.parse("content://photos/2")).apply {
                addItem(ClipData.Item(Uri.parse("content://photos/1")))
                addItem(ClipData.Item(Uri.parse("file:///data/data/$own/databases/mailr.db")))
                addItem(ClipData.Item(Uri.parse("content://$own.files/attachments/x")))
            }
            putExtra(Intent.EXTRA_EMAIL, arrayOf("a@b.c"))
        }
        val s = ShareParser.parse(i, own)!!
        assertEquals("Subject", s.subject)
        assertEquals("Hello", s.text)
        // Deduplicated, and never our own files or file:// paths.
        assertEquals(listOf("content://photos/1", "content://photos/2"), s.files.map { it.toString() })
        assertEquals(listOf("a@b.c"), s.to)
        assertNull(ShareParser.parse(Intent(Intent.ACTION_VIEW), own))
    }

    @Test fun composeShareDropsBodyThatRepeatsTheSubject() {
        val a = ShareComposeActivity.argsFor(SharedContent(title = "Only title", text = "Only title"))
        assertEquals("Only title", a.subject)
        assertNull(a.body)
        val b = ShareComposeActivity.argsFor(SharedContent(subject = "Page", text = "https://x.org", to = listOf("a@b.c")))
        assertEquals("https://x.org", b.body)
        assertEquals("a@b.c", b.to)
    }

    @Test fun composerLinksBareUrls() {
        assertEquals("<p>see <a href=\"https://x.org/a?b=1&amp;c=2\">https://x.org/a?b=1&amp;c=2</a>.</p>",
            Format.textToHtml("see https://x.org/a?b=1&c=2."))
    }

    // ---------------- the send pipeline ----------------

    @Test fun sendsToDefaultIdentityWithFetchedTitle() = runTest {
        val id = repo.create(SharedContent(text = "https://example.com/story"), resolver()).also { repo.confirm(it) }  // the Send tap
        assertEquals(listOf(id), enqueued)
        assertEquals(SelfSendRepository.Outcome.DONE, repo.process(id))
        val body = sendBody()
        assertEquals("me@example.com", body.payload.to.single().email)
        assertEquals(9L, body.payload.identityId)
        assertEquals("Fetched Page Title", body.payload.subject)
        assertEquals("https://example.com/story", body.payload.text)
        val row = repo.get(id)!!
        assertEquals(SelfState.HELD, row.state)
        assertEquals(41L, row.outboxId)
        // The Idempotency-Key is the row's send key, so a retried POST cannot send twice.
        assertEquals(row.sendKey, requests.last { it.path == "/api/v1/send" }.getHeader("Idempotency-Key"))
    }

    @Test fun offlineRetriesWithTheSameKeyAndUndoBeforeSendingStopsLocally() = runTest {
        val id = repo.create(SharedContent(subject = "S", text = "t"), resolver()).also { repo.confirm(it) }  // the Send tap
        offline = true
        assertEquals(SelfSendRepository.Outcome.RETRY, repo.process(id))
        assertEquals(SelfState.QUEUED, repo.get(id)!!.state)
        assertEquals(UndoResult.STOPPED, repo.cancel(id))
        assertEquals(SelfState.CANCELLED, repo.get(id)!!.state)
        offline = false
        // The worker's next run finds it cancelled and sends nothing.
        assertEquals(SelfSendRepository.Outcome.DONE, repo.process(id))
        assertTrue(requests.none { it.path == "/api/v1/send" })
    }

    @Test fun undoAfterTheServerHasItCancelsTheOutbox() = runTest {
        val id = repo.create(SharedContent(subject = "S", text = "t"), resolver()).also { repo.confirm(it) }  // the Send tap
        repo.process(id)
        assertEquals(UndoResult.STOPPED, repo.cancel(id))
        assertTrue(requests.any { it.path == "/api/v1/outbox/41/cancel" })
        assertEquals(SelfState.CANCELLED, repo.get(id)!!.state)
    }

    @Test fun undoTooLateIsReported() = runTest {
        val id = repo.create(SharedContent(subject = "S", text = "t"), resolver()).also { repo.confirm(it) }  // the Send tap
        repo.process(id)
        cancelStatus = 409
        assertEquals(UndoResult.TOO_LATE, repo.cancel(id))
        assertEquals(SelfState.HELD, repo.get(id)!!.state)
    }

    @Test fun addingANoteResendsWithANewKey() = runTest {
        val id = repo.create(SharedContent(subject = "S", text = "https://x.org"), resolver()).also { repo.confirm(it) }  // the Send tap
        repo.process(id)
        val firstKey = repo.get(id)!!.sendKey
        assertEquals(UndoResult.STOPPED, repo.pauseForNote(id))
        assertEquals(SelfState.EDITING, repo.get(id)!!.state)
        repo.sendWithNote(id, "  read later  ")
        assertEquals(SelfState.QUEUED, repo.get(id)!!.state)
        repo.process(id)
        assertEquals("read later\n\nhttps://x.org", sendBody().payload.text)
        val keys = requests.filter { it.path == "/api/v1/send" }.map { it.getHeader("Idempotency-Key") }
        assertEquals(2, keys.toSet().size)
        assertEquals(firstKey, keys.first())
    }

    /** The note typed on the card goes with the message on the Send tap. */
    @Test fun theNoteTypedBeforeSendGoesWithTheMessage() = runTest {
        val id = repo.create(SharedContent(subject = "Screenshot", text = "https://x.org"), resolver())
        repo.confirm(id, note = "  How was it not able to find this?  ")
        assertEquals("How was it not able to find this?", repo.get(id)!!.note)
        repo.process(id)
        assertEquals("How was it not able to find this?\n\nhttps://x.org", sendBody().payload.text)
    }

    @Test fun noteOnlyFromTheLauncherShortcut() = runTest {
        val id = repo.create(SharedContent(), resolver(), startEditing = true)
        assertTrue(enqueued.isEmpty())
        repo.sendWithNote(id, "Call the bank\nabout the card")
        repo.process(id)
        assertEquals("Call the bank", sendBody().payload.subject)
    }

    private fun resolver() = ApplicationProvider.getApplicationContext<Application>().contentResolver
}
