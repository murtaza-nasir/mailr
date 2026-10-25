// ACT-SURFACES: android.reader
package io.github.murtazanasir.mailr

import android.app.Application
import androidx.test.core.app.ApplicationProvider
import io.github.murtazanasir.mailr.data.api.ApiClient
import io.github.murtazanasir.mailr.data.api.Attachment
import io.github.murtazanasir.mailr.data.api.MailrApi
import io.github.murtazanasir.mailr.data.auth.SessionStore
import io.github.murtazanasir.mailr.data.files.AttachmentCache
import kotlinx.coroutines.runBlocking
import okhttp3.mockwebserver.MockResponse
import okhttp3.mockwebserver.MockWebServer
import okhttp3.mockwebserver.SocketPolicy
import okio.Buffer
import org.junit.After
import org.junit.Assert.assertArrayEquals
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Assert.fail
import org.junit.Test
import org.junit.runner.RunWith
import org.robolectric.RobolectricTestRunner
import org.robolectric.annotation.Config
import java.io.File

/**
 * An attachment opened, shared or saved is downloaded whole before it is used: a download cut off
 * half way leaves no file under the attachment's name, so the next tap downloads it again instead
 * of handing a broken file to the viewer.
 */
@RunWith(RobolectricTestRunner::class)
@Config(application = Application::class, sdk = [34])
class AttachmentCacheTest {
    private val server = MockWebServer()
    private val bytes = ByteArray(64 * 1024) { (it % 251).toByte() }
    private val pdf = Attachment(part = 2, filename = "report.pdf", type = "application/pdf", size = bytes.size.toLong())

    @After fun stop() = server.shutdown()

    private fun cache(root: File): AttachmentCache {
        val sessions = SessionStore(ApplicationProvider.getApplicationContext())
        sessions.server = server.url("/").toString()
        sessions.save("tok", "csrf", "me@example.com", "Me")
        val client = ApiClient(sessions)
        return AttachmentCache(root, client, MailrApi(client))
    }

    private fun whole() = MockResponse().setHeader("Content-Type", "application/pdf").setBody(Buffer().write(bytes))

    @Test fun aDownloadCutOffHalfWayIsNeverReused() = runBlocking {
        val root = File(ApplicationProvider.getApplicationContext<Application>().cacheDir, "t-${System.nanoTime()}")  // clock-ok: unique folder
        val cache = cache(root)
        server.enqueue(whole().setSocketPolicy(SocketPolicy.DISCONNECT_DURING_RESPONSE_BODY))
        server.enqueue(whole())
        try {
            cache.file(42, pdf)
            fail("a download cut off half way must fail")
        } catch (e: java.io.IOException) {
            // expected
        }
        // Nothing under the attachment's name, and no partial file left behind.
        val left = root.walkTopDown().filter { it.isFile }.toList()
        assertTrue("left behind: $left", left.isEmpty())
        val f = cache.file(42, pdf)
        assertArrayEquals(bytes, f.readBytes())
        assertEquals(2, server.requestCount)
        // A whole file is reused.
        assertArrayEquals(bytes, cache.file(42, pdf).readBytes())
        assertEquals(2, server.requestCount)
    }

    @Test fun twoAttachmentsWithTheSameNameKeepTheirOwnFiles() = runBlocking {
        val root = File(ApplicationProvider.getApplicationContext<Application>().cacheDir, "n-${System.nanoTime()}")  // clock-ok: unique folder
        val cache = cache(root)
        val other = ByteArray(1000) { 7 }
        server.enqueue(whole())
        server.enqueue(MockResponse().setHeader("Content-Type", "application/pdf").setBody(Buffer().write(other)))
        val first = cache.file(44, pdf)
        val second = cache.file(44, pdf.copy(part = 3))
        assertTrue(first != second)
        assertEquals("report.pdf", second.name)
        assertArrayEquals(bytes, first.readBytes())
        assertArrayEquals(other, second.readBytes())
        assertEquals(2, server.requestCount)
    }

    @Test fun aShortBodyAgainstContentLengthFails() = runBlocking {
        val root = File(ApplicationProvider.getApplicationContext<Application>().cacheDir, "s-${System.nanoTime()}")  // clock-ok: unique folder
        val cache = cache(root)
        // The server promises more than it sends and closes the connection.
        server.enqueue(MockResponse().setBody(Buffer().write(bytes)).setHeader("Content-Length", bytes.size + 10)
            .setSocketPolicy(SocketPolicy.DISCONNECT_AT_END))
        try {
            cache.file(43, pdf)
            fail("a short download must fail")
        } catch (e: java.io.IOException) {
            // expected
        }
        assertTrue(root.walkTopDown().none { it.isFile })
    }
}
