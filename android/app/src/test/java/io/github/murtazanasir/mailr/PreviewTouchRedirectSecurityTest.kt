package io.github.murtazanasir.mailr

import android.app.Application
import android.view.MotionEvent
import androidx.compose.ui.graphics.ImageBitmap
import androidx.test.core.app.ApplicationProvider
import io.github.murtazanasir.mailr.data.api.ConversationSummary
import io.github.murtazanasir.mailr.data.api.MailrJson
import io.github.murtazanasir.mailr.data.api.Message
import io.github.murtazanasir.mailr.data.share.PublicDns
import io.github.murtazanasir.mailr.ui.components.PictureCache
import io.github.murtazanasir.mailr.ui.components.TouchGuard
import kotlinx.coroutines.runBlocking
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
import java.net.InetAddress

/** Android security fixes: previews, obscured touches, redirects and the picture cache. */
@RunWith(RobolectricTestRunner::class)
@Config(application = Application::class, sdk = [34])
class PreviewTouchRedirectSecurityTest {
    @get:Rule(order = Int.MIN_VALUE) val graphs = TestGraph()

    // ---------------- Previews lose bidi isolates and control characters ----------------

    @Test fun snippetsAreCleanedOnDecode() {
        val conv = MailrJson.decodeFromString(ConversationSummary.serializer(),
            """{"id":1,"subject":"Hi","snippet":"pay ⁧evil⁩ now\u0007\nline two"}""")
        assertEquals("pay evil now\nline two", conv.snippet)
        val msg = MailrJson.decodeFromString(Message.serializer(),
            """{"id":2,"snippet":"⁦a‮b‏c"}""")
        assertEquals("abc", msg.snippet)
    }

    // ---------------- A partly covered window refuses the touch ----------------

    @Test fun partiallyObscuredTouchesAreRefused() {
        assertTrue(TouchGuard.isObscured(MotionEvent.FLAG_WINDOW_IS_OBSCURED))
        assertTrue(TouchGuard.isObscured(MotionEvent.FLAG_WINDOW_IS_PARTIALLY_OBSCURED))
        assertFalse(TouchGuard.isObscured(0))
        val props = arrayOf(MotionEvent.PointerProperties().apply { id = 0; toolType = MotionEvent.TOOL_TYPE_FINGER })
        val coords = arrayOf(MotionEvent.PointerCoords().apply { x = 1f; y = 1f; pressure = 1f; size = 1f })
        val ev = MotionEvent.obtain(0, 0, MotionEvent.ACTION_DOWN, 1, props, coords, 0, 0, 1f, 1f, 0, 0,
            android.view.InputDevice.SOURCE_TOUCHSCREEN, MotionEvent.FLAG_WINDOW_IS_PARTIALLY_OBSCURED)
        assertTrue(TouchGuard.isObscured(ev))
        ev.recycle()
    }

    // ---------------- Embedded IPv4 forms and every redirect hop ----------------

    private fun ip(s: String) = InetAddress.getByName(s)

    @Test fun ipv6FormsCarryingAPrivateIpv4AreNotPublic() {
        assertFalse(PublicDns.isPublic(ip("2002:c0a8:0101::1")))        // 6to4 of 192.168.1.1
        assertFalse(PublicDns.isPublic(ip("2002:7f00:0001::")))          // 6to4 of 127.0.0.1
        assertTrue(PublicDns.isPublic(ip("2002:0808:0808::1")))          // 6to4 of 8.8.8.8
        assertFalse(PublicDns.isPublic(ip("64:ff9b::a00:1")))             // NAT64 of 10.0.0.1
        assertTrue(PublicDns.isPublic(ip("64:ff9b::808:808")))            // NAT64 of 8.8.8.8
        assertFalse(PublicDns.isPublic(ip("64:ff9b:1::808:808")))         // local-use NAT64
        // Teredo: client 192.168.1.1 is stored inverted (3f57:fefe); server 8.8.8.8.
        assertFalse(PublicDns.isPublic(ip("2001:0:808:808::3f57:fefe")))
        assertTrue(PublicDns.isPublic(ip("2001:0:808:808::f7f7:f7f7")))   // client 8.8.8.8
        assertFalse(PublicDns.isPublic(ip("2001:0:a00:1::f7f7:f7f7")))    // server 10.0.0.1
        assertFalse(PublicDns.isPublic(ip("::ffff:10.0.0.1")))            // IPv4-mapped
        assertFalse(PublicDns.isPublic(ip("::10.0.0.1")))                 // IPv4-compatible
        assertFalse(PublicDns.isPublic(ip("2001:db8::1")))                // documentation
        assertTrue(PublicDns.isPublic(ip("2606:4700:4700::1111")))        // ordinary public IPv6
        assertFalse(PublicDns.fetchable("https://[2002:c0a8:0101::1]/"))
    }

    @Test fun eachRedirectHopIsChecked() {
        val from = "https://example.com/a"
        assertEquals("https://example.org/b", PublicDns.redirectTarget(from, "https://example.org/b"))
        assertEquals("https://example.com/next", PublicDns.redirectTarget(from, "/next"))
        assertNull(PublicDns.redirectTarget(from, "https://example.com:8443/admin"))
        assertNull(PublicDns.redirectTarget(from, "http://example.com/"))
        assertNull(PublicDns.redirectTarget(from, "https://router.lan/"))
        assertNull(PublicDns.redirectTarget(from, "https://127.0.0.1/"))
        assertNull(PublicDns.redirectTarget(from, "https://[64:ff9b::a00:1]/"))
        assertNull(PublicDns.redirectTarget(from, null))
    }

    // ---------------- Sign-out clears the picture cache ----------------

    @Test fun wipeLocalClearsPictures() = runBlocking {
        val app = ApplicationProvider.getApplicationContext<Application>()
        val g = graphs.graph(app)
        PictureCache.putForTest("/api/v1/me/picture?v=1", ImageBitmap(1, 1))
        assertNotNull(PictureCache.cachedForTest("/api/v1/me/picture?v=1"))
        g.wipeLocal(signOut = true)
        assertNull(PictureCache.cachedForTest("/api/v1/me/picture?v=1"))
        graphs.close()
    }
}
