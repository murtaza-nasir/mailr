package io.github.murtazanasir.mailr

import android.app.Application
import android.provider.Settings
import androidx.test.core.app.ApplicationProvider
import io.github.murtazanasir.mailr.data.api.ApiClient
import io.github.murtazanasir.mailr.data.auth.SessionStore
import kotlinx.coroutines.test.runTest
import okhttp3.Request
import okhttp3.mockwebserver.MockResponse
import okhttp3.mockwebserver.MockWebServer
import org.junit.After
import org.junit.Assert.assertEquals
import org.junit.Before
import org.junit.Test
import org.junit.runner.RunWith
import org.robolectric.RobolectricTestRunner
import org.robolectric.annotation.Config

/**
 * Ambrose wrote "today at 17:08" to someone whose app shows 5:08 PM (20 Oct 2026). The phone says
 * its zone and clock on every request to the server (X-Mailr-Zone, X-Mailr-Clock), as the web does;
 * the server keeps them for Region and Time format on Automatic (backend calendar/tz.py).
 */
@RunWith(RobolectricTestRunner::class)
@Config(application = Application::class, sdk = [34])
class DeviceClockHeaderTest {
    private lateinit var server: MockWebServer
    private val ctx get() = ApplicationProvider.getApplicationContext<Application>()

    @Before fun setUp() {
        server = MockWebServer().apply { start() }
    }

    @After fun tearDown() = server.shutdown()

    private fun client(): ApiClient {
        val sessions = SessionStore(ctx).apply {
            server = this@DeviceClockHeaderTest.server.url("/").toString()
            save("tok-1", "csrf-1", "me@example.com", "Me")
        }
        return ApiClient(sessions, deviceZone = { "America/Chicago" }, deviceClock = { ApiClient.clockOf(ctx) })
    }

    @Test fun zoneAndClockGoToTheServer() = runTest {
        Settings.System.putString(ctx.contentResolver, Settings.System.TIME_12_24, "12")
        val c = client()
        server.enqueue(MockResponse().setBody("{}"))
        c.raw(Request.Builder().url(c.url("me")).build()).close()
        val got = server.takeRequest()
        assertEquals("America/Chicago", got.getHeader("X-Mailr-Zone"))
        assertEquals("12h", got.getHeader("X-Mailr-Clock"))

        // Read for each request: a change in the system settings shows on the next one.
        Settings.System.putString(ctx.contentResolver, Settings.System.TIME_12_24, "24")
        server.enqueue(MockResponse().setBody("{}"))
        c.raw(Request.Builder().url(c.url("me")).build()).close()
        assertEquals("24h", server.takeRequest().getHeader("X-Mailr-Clock"))
    }
}
