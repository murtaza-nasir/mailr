// ACT-SURFACES: android.setup
package io.github.murtazanasir.mailr

import android.app.Application
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Surface
import androidx.compose.ui.Modifier
import io.github.murtazanasir.mailr.testing.createComposeRule
import androidx.compose.ui.test.onNodeWithTag
import androidx.compose.ui.test.onNodeWithText
import androidx.compose.ui.test.onRoot
import androidx.compose.ui.test.performClick
import androidx.compose.ui.test.performScrollTo
import io.github.murtazanasir.mailr.data.push.PushStatus
import io.github.murtazanasir.mailr.data.settings.ThemeMode
import io.github.murtazanasir.mailr.ui.onboarding.PhoneSetupCallbacks
import io.github.murtazanasir.mailr.ui.onboarding.PhoneSetupItem
import io.github.murtazanasir.mailr.ui.onboarding.PhoneSetupScreen
import io.github.murtazanasir.mailr.ui.onboarding.PhoneSetupState
import io.github.murtazanasir.mailr.ui.onboarding.done
import io.github.murtazanasir.mailr.ui.onboarding.phoneSetupNeeded
import io.github.murtazanasir.mailr.ui.theme.MailrTheme
import com.github.takahirom.roborazzi.captureRoboImage
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Rule
import org.junit.Test
import org.junit.runner.RunWith
import org.robolectric.RobolectricTestRunner
import org.robolectric.RuntimeEnvironment
import org.robolectric.annotation.Config
import org.robolectric.annotation.GraphicsMode

/** The short setup after the first sign-in (ui/onboarding/PhoneSetup.kt): what is done, and each button's action. */
@RunWith(RobolectricTestRunner::class)
@GraphicsMode(GraphicsMode.Mode.NATIVE)
@Config(application = Application::class, sdk = [34])
class PhoneSetupTest {
    @get:Rule val compose = createComposeRule()
    private val clicks = mutableListOf<String>()
    private val cb = PhoneSetupCallbacks(allowNotifications = { clicks += "notify" }, openBattery = { clicks += "battery" },
        getPushApp = { clicks += "get" }, choosePushApp = { clicks += "choose" }, finish = { clicks += "finish" },
        copyPushServer = { clicks += "copy" }, useLive = { clicks += "live" })

    private fun show(s: PhoneSetupState, shot: String, dark: Boolean = false) {
        RuntimeEnvironment.setQualifiers("w411dp-h891dp-port-${if (dark) "night" else "notnight"}-xxhdpi")
        compose.setContent {
            MailrTheme(if (dark) ThemeMode.DARK else ThemeMode.LIGHT, dynamic = false) {
                Surface(Modifier.fillMaxSize(), color = MaterialTheme.colorScheme.surface) { PhoneSetupScreen(s, cb) }
            }
        }
        compose.onRoot().captureRoboImage("screenshots/phone-setup-$shot.png")
    }

    private val fresh = PhoneSetupState(sdk = 34, notificationsAllowed = false, batteryExempt = false, push = PushStatus.NO_DISTRIBUTOR)

    @Test fun whatIsDone() {
        assertTrue(phoneSetupNeeded(fresh))
        val all = fresh.copy(notificationsAllowed = true, batteryExempt = true, push = PushStatus.CONNECTED)
        assertFalse(phoneSetupNeeded(all))
        assertTrue(fresh.copy(push = PushStatus.REGISTERING).done(PhoneSetupItem.PUSH))
        assertFalse(fresh.copy(push = PushStatus.FAILED).done(PhoneSetupItem.PUSH))
    }

    /** notification.subscribe on the phone: every step is offered until it is done. */
    @Test fun freshPhoneOffersEveryStep() {
        show(fresh, "fresh")
        compose.onNodeWithText("Phone setup").assertExists()
        compose.onNodeWithTag("phone-setup-notifications-action").performClick()
        compose.onNodeWithTag("phone-setup-battery-action").performClick()
        compose.onNodeWithTag("phone-setup-push-action").performScrollTo().performClick()
        compose.onNodeWithTag("phone-setup-finish").performScrollTo().performClick()
        assertEquals(listOf("notify", "battery", "get", "finish"), clicks)
        compose.onNodeWithText("Done for now").assertExists()
    }

    @Test fun severalPushAppsAskWhichOne() {
        show(fresh.copy(notificationsAllowed = true, push = PushStatus.CHOOSE), "choose", dark = true)
        compose.onNodeWithText("Notifications are on.").assertExists()
        compose.onNodeWithTag("phone-setup-notifications-action").assertDoesNotExist()
        compose.onNodeWithTag("phone-setup-push-action").performScrollTo().performClick()
        assertEquals(listOf("choose"), clicks)
    }

    /** No push app: the live connection is offered with its cost, and counts as done once on. */
    @Test fun liveConnectionInstead() {
        show(fresh.copy(notificationsAllowed = true, batteryExempt = true), "live")
        compose.onNodeWithTag("phone-setup-push-live").performScrollTo().performClick()
        assertEquals(listOf("live"), clicks)
        assertTrue(fresh.copy(live = true).done(PhoneSetupItem.PUSH))
        assertFalse(phoneSetupNeeded(fresh.copy(notificationsAllowed = true, batteryExempt = true, live = true)))
    }

    /** The server's own ntfy: one tap copies the address and opens ntfy. */
    @Test fun ownNtfyIsOneTap() {
        show(fresh.copy(notificationsAllowed = true, batteryExempt = true, pushHint = "This server runs its own ntfy."), "own-ntfy")
        compose.onNodeWithTag("phone-setup-push-copy").performScrollTo().performClick()
        assertEquals(listOf("copy"), clicks)
    }

    @Test fun allDone() {
        show(PhoneSetupState(34, true, true, PushStatus.CONNECTED, "ntfy"), "done")
        compose.onNodeWithText("Instant mail is working, through ntfy.").assertExists()
        compose.onNodeWithText("Go to my inbox").assertExists()
    }
}
