// ACT-SURFACES: android.reader
package io.github.murtazanasir.mailr

import android.app.Application
import androidx.compose.material3.MaterialTheme
import androidx.compose.ui.test.assertIsDisplayed
import io.github.murtazanasir.mailr.testing.createComposeRule
import androidx.compose.ui.test.onAllNodesWithText
import androidx.compose.ui.test.assertCountEquals
import androidx.compose.ui.test.onNodeWithText
import androidx.compose.ui.test.performClick
import io.github.murtazanasir.mailr.data.api.ApiClient
import io.github.murtazanasir.mailr.data.api.MailrApi
import io.github.murtazanasir.mailr.data.api.MessageBody
import io.github.murtazanasir.mailr.data.api.Trackers
import io.github.murtazanasir.mailr.data.auth.SessionStore
import io.github.murtazanasir.mailr.ui.conversation.BodyRequests
import io.github.murtazanasir.mailr.ui.conversation.TrackersBlocked
import io.github.murtazanasir.mailr.ui.conversation.wrap
import androidx.test.core.app.ApplicationProvider
import okhttp3.mockwebserver.MockResponse
import okhttp3.mockwebserver.MockWebServer
import okio.Buffer
import org.junit.After
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNotNull
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Rule
import org.junit.Test
import org.junit.runner.RunWith
import org.robolectric.RobolectricTestRunner
import org.robolectric.annotation.Config

/**
 * ACT mail.show_images on android.reader: the message WebView never requests a remote host.
 * Only the image proxy's paths under the body's made-up origin are answered, through the app's
 * signed-in client; every other request gets an empty response without touching the network.
 * The reader shows "N trackers blocked" with the hosts.
 */
@RunWith(RobolectricTestRunner::class)
@Config(application = Application::class, sdk = [34])
class ImageProxyTest {
    @get:Rule val compose = createComposeRule()
    private val server = MockWebServer()
    private val hash = "0123456789abcdef0123456789abcdef"

    @After fun stop() = server.shutdown()

    @Test fun onlyProxyPathsUnderTheBodyOriginAreAnswered() {
        val fetched = mutableListOf<String>()
        val fetch: (String) -> BodyRequests.Image? = { p -> fetched += p; BodyRequests.Image("image/png", byteArrayOf(1, 2, 3)) }
        val ok = BodyRequests.intercept("${BodyRequests.ORIGIN}/api/v1/messages/42/images/$hash", fetch)
        assertEquals(200, ok!!.statusCode)
        assertEquals("image/png", ok.mimeType)
        assertEquals(listOf("/api/v1/messages/42/images/$hash"), fetched)
        // A sender's server, a tracker, another origin or another mailr path: blocked, never fetched.
        for (url in listOf("https://cdn.example.org/hero.png", "http://example.us1.list-manage.com/track/open.php",
                "https://body.mailr.invalid/api/v1/messages/42/raw", "https://body.mailr.invalid/api/v1/messages/42/images/xyz",
                "https://body.mailr.invalid:8443/api/v1/messages/42/images/$hash",
                "https://body.mailr.invalid/api/v1/messages/42/images/$hash?x=1",
                "https://evil.example/api/v1/messages/42/images/$hash", "file:///etc/hosts")) {
            val r = BodyRequests.intercept(url, fetch)
            assertNotNull(url, r)
            assertEquals(url, 403, r!!.statusCode)
        }
        assertEquals(1, fetched.size)
        // SVG or anything else the fetcher might return is not passed on.
        val svg = BodyRequests.intercept("${BodyRequests.ORIGIN}/api/v1/messages/42/images/$hash") { BodyRequests.Image("image/svg+xml", byteArrayOf(1)) }
        assertEquals(403, svg!!.statusCode)
        // Inline data: images stay with the WebView.
        assertNull(BodyRequests.intercept("data:image/png;base64,AAAA", fetch))
    }

    @Test fun theCspAllowsOnlyTheBodyOrigin() {
        val allowed = wrap("<p>x</p>", showQuote = false, darkText = true, allowImages = true)
        assertTrue(allowed.contains("img-src data: ${BodyRequests.ORIGIN};"))
        assertFalse(allowed.contains("https:;") || allowed.contains(" http:"))
        assertTrue(wrap("<p>x</p>", false, true, allowImages = false).contains("img-src data:;"))
    }

    @Test fun proxiedImagesComeThroughTheSignedInClient() {
        server.enqueue(MockResponse().setHeader("Content-Type", "image/png").setBody(Buffer().write(byteArrayOf(9, 8, 7))))
        val sessions = SessionStore(ApplicationProvider.getApplicationContext())
        sessions.server = server.url("/").toString()
        sessions.save("tok", "csrf", "me@example.com", "Me")
        val api = MailrApi(ApiClient(sessions))
        val (type, bytes) = api.proxiedImage("/api/v1/messages/42/images/$hash")!!
        assertEquals("image/png", type)
        assertEquals(3, bytes.size)
        val req = server.takeRequest()
        assertEquals("/api/v1/messages/42/images/$hash", req.path)
        assertTrue(req.getHeader("Cookie")!!.contains("mailr_session=tok"))
        server.enqueue(MockResponse().setHeader("Content-Type", "image/svg+xml").setBody("<svg/>"))
        assertNull(api.proxiedImage("/api/v1/messages/42/images/$hash"))
    }

    @Test fun theReaderShowsTrackersBlocked() {
        val body = MessageBody(html = "<p>Hi</p>", proxied = true,
            trackers = Trackers(2, listOf("e2e.us1.list-manage.com", "pixel.example.net")), linksUnwrapped = 1)
        compose.setContent { MaterialTheme { TrackersBlocked(body) } }
        compose.onNodeWithText("2 trackers blocked").assertIsDisplayed().performClick()
        compose.onNodeWithText("• e2e.us1.list-manage.com").assertIsDisplayed()
        compose.onNodeWithText("• pixel.example.net").assertIsDisplayed()
        compose.onNodeWithText("Close").performClick()
        compose.onAllNodesWithText("• pixel.example.net").assertCountEquals(0)
        assertEquals("1 tracker blocked", BodyRequests.trackersLabel(1))
    }

    @Test fun nothingShowsWithoutTrackers() {
        compose.setContent { MaterialTheme { TrackersBlocked(MessageBody(html = "<p>Hi</p>")) } }
        compose.onAllNodesWithText("blocked", substring = true).assertCountEquals(0)
    }
}
