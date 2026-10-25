// ACT-SURFACES: android.settings
package io.github.murtazanasir.mailr

import android.app.Application
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Surface
import androidx.compose.ui.Modifier
import androidx.compose.ui.test.assertIsNotEnabled
import io.github.murtazanasir.mailr.testing.createComposeRule
import androidx.compose.ui.test.onNodeWithTag
import androidx.compose.ui.test.onNodeWithText
import androidx.compose.ui.test.onRoot
import androidx.compose.ui.test.performClick
import io.github.murtazanasir.mailr.data.push.PushInfo
import io.github.murtazanasir.mailr.data.push.PushStatus
import io.github.murtazanasir.mailr.data.settings.ThemeMode
import io.github.murtazanasir.mailr.ui.settings.Header
import io.github.murtazanasir.mailr.ui.settings.PushCallbacks
import io.github.murtazanasir.mailr.ui.settings.PushUi
import io.github.murtazanasir.mailr.ui.settings.pushItems
import io.github.murtazanasir.mailr.ui.settings.pushServerHint
import io.github.murtazanasir.mailr.ui.settings.pushStatusText
import io.github.murtazanasir.mailr.ui.settings.liveConnectionShown
import io.github.murtazanasir.mailr.ui.settings.liveConnectionText
import io.github.murtazanasir.mailr.data.push.LiveConnection
import io.github.murtazanasir.mailr.ui.theme.MailrTheme
import com.github.takahirom.roborazzi.captureRoboImage
import org.junit.Assert.assertEquals
import org.junit.Rule
import org.junit.Test
import org.junit.runner.RunWith
import org.robolectric.ParameterizedRobolectricTestRunner
import org.robolectric.RuntimeEnvironment
import org.robolectric.annotation.Config
import org.robolectric.annotation.GraphicsMode

/**
 * Settings → Notifications → Push in each state, light and dark: connected (with Send test and
 * its answer), no push app, several push apps, registration failed. Each state's button calls
 * its action.
 */
@RunWith(ParameterizedRobolectricTestRunner::class)
@GraphicsMode(GraphicsMode.Mode.NATIVE)
@Config(application = Application::class, sdk = [34])
class PushSettingsTest(private val dark: Boolean) {
    companion object {
        @JvmStatic
        @ParameterizedRobolectricTestRunner.Parameters(name = "dark={0}")
        fun params(): List<Array<Any>> = listOf(arrayOf<Any>(false), arrayOf<Any>(true))
    }

    @get:Rule val compose = createComposeRule()
    private val mode get() = if (dark) "dark" else "light"
    private val clicks = mutableListOf<String>()
    private val cb = PushCallbacks(retry = { clicks += "retry" }, choose = { clicks += "choose" },
        getApp = { clicks += "get" }, test = { clicks += "test" }, copyServer = { clicks += "copy" },
        setLive = { v -> clicks += "live:$v" })

    /** [shot] null: the same screen as another test's shot, so no second reference. */
    private fun rows(ui: PushUi, shot: String?) {
        RuntimeEnvironment.setQualifiers("w411dp-h891dp-port-${if (dark) "night" else "notnight"}-xxhdpi")
        compose.setContent {
            MailrTheme(if (dark) ThemeMode.DARK else ThemeMode.LIGHT, dynamic = false) {
                Surface(Modifier.fillMaxSize(), color = MaterialTheme.colorScheme.surface) {
                    LazyColumn { item { Header("Notifications") }; pushItems(ui, cb) }
                }
            }
        }
        if (shot != null) compose.onRoot().captureRoboImage("screenshots/settings-push-$mode-$shot.png")
    }

    private val ntfy = PushInfo(distributor = "io.heckel.ntfy", distributorName = "ntfy", targetId = 7)

    @Test fun connected() {
        val info = ntfy.copy(status = PushStatus.CONNECTED)
        rows(PushUi(info, testResult = "Sent. The test notification should appear in a few seconds."), "connected")
        compose.onNodeWithText(pushStatusText(info)).assertExists()
        compose.onNodeWithText("Sent. The test notification should appear in a few seconds.").assertExists()
        compose.onNodeWithText("Delivered by push").assertExists()
        compose.onNodeWithTag("push-test").performClick()
        assertEquals(listOf("test"), clicks)
        compose.onNodeWithTag("push-action").assertDoesNotExist()
    }

    @Test fun testing() {
        rows(PushUi(ntfy.copy(status = PushStatus.CONNECTED), testing = true), "testing")
        compose.onNodeWithText("Sending…").assertExists()
        compose.onNodeWithTag("push-test").assertIsNotEnabled()
    }

    @Test fun noPushApp() {
        val info = PushInfo(status = PushStatus.NO_DISTRIBUTOR)
        rows(PushUi(info), "none")
        compose.onNodeWithText(pushStatusText(info)).assertExists()
        compose.onNodeWithText("Test push").assertDoesNotExist()
        compose.onNodeWithText("Get ntfy").performClick()
        assertEquals(listOf("get"), clicks)
    }

    @Test fun severalPushApps() {
        rows(PushUi(PushInfo(status = PushStatus.CHOOSE)), "choose")
        compose.onNodeWithText("Choose").performClick()
        assertEquals(listOf("choose"), clicks)
    }

    @Test fun registrationFailed() {
        val info = ntfy.copy(status = PushStatus.FAILED, error = "No connection to the push server")
        rows(PushUi(info), "failed")
        compose.onNodeWithText("Registration failed: No connection to the push server. Mail is checked every 15 minutes until push works.").assertExists()
        compose.onNodeWithText("Retry").performClick()
        assertEquals(listOf("retry"), clicks)
    }

    @Test fun statusWording() {
        assertEquals("Instant mail is working, through ntfy. Mail and approvals arrive at once; mail is also checked hourly.",
            pushStatusText(ntfy.copy(status = PushStatus.CONNECTED)))
        assertEquals("Connecting through ntfy…", pushStatusText(ntfy.copy(status = PushStatus.REGISTERING)))
        assertEquals("No notification app. Install ntfy, a free app, for instant mail. Until then, mail is checked every 15 minutes.",
            pushStatusText(PushInfo(status = PushStatus.NO_DISTRIBUTOR)))
    }

    private val own = io.github.murtazanasir.mailr.data.api.PushServer("https://ntfy.example.com", "ntfy.example.com")

    /** The server's own ntfy, with its address to copy, until push goes through it. */
    @Test fun serverOwnNtfy() {
        rows(PushUi(PushInfo(status = PushStatus.NO_DISTRIBUTOR), server = own), "server")
        compose.onNodeWithTag("push-server").assertExists()
        compose.onNodeWithText("This server has its own ntfy", substring = true).assertExists()
        compose.onNodeWithTag("push-copy-server").performClick()
        assertEquals(listOf("copy"), clicks)
    }

    @Test fun serverHintWording() {
        assertEquals(null, pushServerHint(ntfy.copy(status = PushStatus.CONNECTED, endpoint = "https://ntfy.example.com/up123"), own))
        assertEquals(null, pushServerHint(PushInfo(status = PushStatus.NO_DISTRIBUTOR), null))
        val steps = "1. Tap Copy and open ntfy.\n2. In ntfy, open Settings → Default server.\n3. Paste https://ntfy.example.com, then come back here."
        assertEquals("Push notifications are sent through ntfy.sh, encrypted. To use your server's own ntfy:\n$steps",
            pushServerHint(ntfy.copy(status = PushStatus.CONNECTED, endpoint = "https://ntfy.sh/up123"), own))
        assertEquals("This server has its own ntfy. After installing ntfy:\n$steps",
            pushServerHint(PushInfo(status = PushStatus.NO_DISTRIBUTOR), own))
    }

    /**
     * notification.live_connection: offered with its cost when no push app works; the switch turns it on.
     * The screen is [noPushApp]'s (settings-push-<theme>-none shows the offer).
     */
    @Test fun liveConnectionOffered() {
        val info = PushInfo(status = PushStatus.NO_DISTRIBUTOR)
        rows(PushUi(info), null)
        compose.onNodeWithTag("push-live").assertExists()
        compose.onNodeWithText(liveConnectionText(info, false)).assertExists()
        compose.onNodeWithTag("push-live-switch").performClick()
        assertEquals(listOf("live:true"), clicks)
    }

    @Test fun liveConnectionOn() {
        val info = PushInfo(status = PushStatus.NO_DISTRIBUTOR)
        rows(PushUi(info, live = true), "live-on")
        compose.onNodeWithText("No notification app in use. Mail arrives instantly over the live connection.").assertExists()
        compose.onNodeWithTag("push-live-switch").performClick()
        assertEquals(listOf("live:false"), clicks)
    }

    @Test fun liveConnectionHiddenWhilePushWorks() {
        rows(PushUi(ntfy.copy(status = PushStatus.CONNECTED)), "live-hidden")
        compose.onNodeWithTag("push-live").assertDoesNotExist()
    }

    @Test fun liveConnectionWording() {
        val ok = ntfy.copy(status = PushStatus.CONNECTED)
        assertEquals("Standby: push notifications are sent through ntfy. The live connection is used if push stops working.", liveConnectionText(ok, true))
        assertEquals(true, liveConnectionText(PushInfo(), false).contains("permanent notification"))
        assertEquals(true, liveConnectionText(PushInfo(), false).contains("more battery"))
        assertEquals(true, liveConnectionShown(ok, true))
        assertEquals(false, liveConnectionShown(ok, false))
        assertEquals(true, liveConnectionShown(PushInfo(status = PushStatus.FAILED), false))
    }

    /** When the service runs, and which server events start a check. */
    @Test fun liveConnectionRules() {
        val none = PushInfo(status = PushStatus.NO_DISTRIBUTOR)
        assertEquals(true, LiveConnection.wanted(on = true, signedIn = true, push = none))
        assertEquals(false, LiveConnection.wanted(on = false, signedIn = true, push = none))
        assertEquals(false, LiveConnection.wanted(on = true, signedIn = false, push = none))
        assertEquals(false, LiveConnection.wanted(on = true, signedIn = true, push = ntfy.copy(status = PushStatus.CONNECTED)))
        assertEquals(true, LiveConnection.relevant("notification", null))
        assertEquals(true, LiveConnection.relevant("changes", "conversation"))
        assertEquals(true, LiveConnection.relevant("outbox", null))
        assertEquals(true, LiveConnection.relevant("approval.decided", null))  // the check takes down approvals answered elsewhere
        assertEquals(false, LiveConnection.relevant("changes", "label"))
        assertEquals(false, LiveConnection.relevant("ping", null))
    }
}
