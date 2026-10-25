// ACT-SURFACES: android.settings
package io.github.murtazanasir.mailr

import android.app.Application
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.material3.Surface
import androidx.compose.ui.Modifier
import androidx.compose.ui.test.hasScrollToNodeAction
import androidx.compose.ui.test.hasText
import androidx.compose.ui.test.onNodeWithText
import androidx.compose.ui.test.onRoot
import androidx.compose.ui.test.performClick
import androidx.compose.ui.test.performScrollToNode
import io.github.murtazanasir.mailr.data.push.PushEvent
import io.github.murtazanasir.mailr.data.push.PushPayload
import io.github.murtazanasir.mailr.data.settings.NotifyMode
import io.github.murtazanasir.mailr.data.settings.Prefs
import io.github.murtazanasir.mailr.data.settings.ServerPrefs
import io.github.murtazanasir.mailr.data.settings.ThemeMode
import io.github.murtazanasir.mailr.testing.createComposeRule
import io.github.murtazanasir.mailr.ui.settings.SettingsCallbacks
import io.github.murtazanasir.mailr.ui.settings.SettingsScreen
import io.github.murtazanasir.mailr.ui.theme.MailrTheme
import com.github.takahirom.roborazzi.captureRoboImage
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.JsonPrimitive
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Rule
import org.junit.Test
import org.junit.runner.RunWith
import org.robolectric.RobolectricTestRunner
import org.robolectric.RuntimeEnvironment
import org.robolectric.annotation.Config
import org.robolectric.annotation.GraphicsMode

/**
 * One notification model and the image choice from the web: the phone follows the
 * account's `mail_notifications`, `notify_approvals` and `images` (data/settings/ServerPrefs), keeps
 * only "Mail on this phone" and an image override of its own, and carries its earlier mail choice up
 * once. ACT notification.choose, ACT mail.show_images.
 */
@RunWith(RobolectricTestRunner::class)
@GraphicsMode(GraphicsMode.Mode.NATIVE)
@Config(application = Application::class, sdk = [34])
class NotificationModelTest {
    @get:Rule val compose = createComposeRule()

    private fun settings(vararg kv: Pair<String, Any>) = JsonObject(kv.associate { (k, v) ->
        k to when (v) { is Boolean -> JsonPrimitive(v); else -> JsonPrimitive(v.toString()) } })

    @Test fun thePhoneFollowsTheAccount() {
        val p = ServerPrefs.apply(Prefs(), settings("mail_notifications" to "all", "notify_approvals" to false, "images" to "ask"))
        assertEquals(NotifyMode.ALL, p.notify)
        assertFalse(p.notifyApprovals)
        assertEquals("ask", p.imagesWeb)
        assertFalse(p.showImages())
        // An older server's name for the level.
        assertEquals(NotifyMode.VIP, ServerPrefs.apply(Prefs(), settings("desktop_notifications" to "vip")).notify)
        // Keys the server does not send leave the copy alone.
        assertEquals(NotifyMode.PRIMARY, ServerPrefs.apply(Prefs(notify = NotifyMode.PRIMARY), settings()).notify)
    }

    @Test fun mailOnThisPhoneIsTheOneOverride() {
        val p = Prefs(notify = NotifyMode.ALL, phoneMailOff = true)
        assertEquals(NotifyMode.OFF, p.mailNotify())
        assertEquals(NotifyMode.ALL, p.copy(phoneMailOff = false).mailNotify())
    }

    @Test fun imagesFollowTheWebUnlessThisPhoneSaysOtherwise() {
        assertTrue(Prefs(imagesWeb = "always").showImages())
        assertFalse(Prefs(imagesWeb = "never").showImages())
        assertFalse(Prefs(imagesWeb = "always", imagesOverride = "ask").showImages())
        assertTrue(Prefs(imagesWeb = "ask", imagesOverride = "always").showImages())
    }

    @Test fun thePhonesEarlierChoiceMovesUpOnce() {
        // Primary chosen on the phone, the account still on the default: the account takes it.
        val (p, patch) = ServerPrefs.migrate(Prefs(notify = NotifyMode.PRIMARY), settings("mail_notifications" to "important"))
        assertTrue(p.notifyMigrated)
        assertEquals("primary", (patch!!["mail_notifications"] as JsonPrimitive).content)
        // Off on the phone stays this phone's own: the web keeps notifying.
        val (off, none) = ServerPrefs.migrate(Prefs(notify = NotifyMode.OFF), settings("mail_notifications" to "important"))
        assertTrue(off.phoneMailOff && off.notifyMigrated)
        assertNull(none)
        // The account chose something on the web: it wins.
        assertNull(ServerPrefs.migrate(Prefs(notify = NotifyMode.ALL), settings("mail_notifications" to "vip")).second)
        // Only once.
        assertNull(ServerPrefs.migrate(Prefs(notify = NotifyMode.ALL, notifyMigrated = true), settings("mail_notifications" to "important")).second)
    }

    @Test fun anAccountNoticeIsItsOwnKind() {
        val e = PushPayload.decode("""{"type":"account","title":"Work mail needs its password again","body":"Open Settings","connector_id":4}""".toByteArray())
        assertTrue(e is PushEvent.Account)
        assertEquals(4L, (e as PushEvent.Account).connectorId)
    }

    @Test fun settingsSaveTheAccountChoicesOnTheServer() {
        RuntimeEnvironment.setQualifiers("w411dp-h891dp-port-notnight-xxhdpi")
        var prefs = Prefs(notify = NotifyMode.IMPORTANT, imagesWeb = "always")
        val approvals = mutableListOf<Boolean>()
        compose.setContent {
            MailrTheme(ThemeMode.LIGHT, dynamic = false) {
                Surface(Modifier.fillMaxSize()) {
                    SettingsScreen(prefs, "Alex Morgan", "me@example.com", "https://mail.example.com", emptyList(),
                        SettingsCallbacks({}, { f -> prefs = f(prefs) }, {}, {}, {}, setApprovals = { approvals += it }))
                }
            }
        }
        compose.onNodeWithText("As on the web (Always show)", substring = true).assertExists()
        compose.onNode(hasScrollToNodeAction()).performScrollToNode(hasText("Mail on this phone"))
        compose.onNodeWithText("All devices: this phone, the web and other browsers", substring = true).assertExists()
        compose.onRoot().captureRoboImage("screenshots/settings-notifications-light.png")
        compose.onNodeWithText("Waiting for you").performClick()
        compose.runOnIdle { assertEquals(listOf(false), approvals) }
        compose.onNodeWithText("Mail on this phone").performClick()
        compose.runOnIdle { assertTrue(prefs.phoneMailOff) }
    }
}
