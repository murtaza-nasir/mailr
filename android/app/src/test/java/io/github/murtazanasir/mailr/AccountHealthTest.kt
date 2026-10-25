// ACT-SURFACES: android.settings
package io.github.murtazanasir.mailr

import android.app.Application
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Surface
import androidx.compose.ui.Modifier
import androidx.compose.ui.test.onNodeWithTag
import androidx.compose.ui.test.onNodeWithText
import androidx.compose.ui.test.onRoot
import androidx.compose.ui.test.performClick
import io.github.murtazanasir.mailr.data.api.AccountHealth
import io.github.murtazanasir.mailr.data.api.AccountHealthText
import io.github.murtazanasir.mailr.data.api.MailAccount
import io.github.murtazanasir.mailr.data.api.MailrJson
import io.github.murtazanasir.mailr.data.api.isMail
import io.github.murtazanasir.mailr.data.settings.ThemeMode
import io.github.murtazanasir.mailr.testing.createComposeRule
import io.github.murtazanasir.mailr.ui.settings.Header
import io.github.murtazanasir.mailr.ui.settings.accountItems
import io.github.murtazanasir.mailr.ui.theme.MailrTheme
import com.github.takahirom.roborazzi.captureRoboImage
import kotlinx.serialization.builtins.ListSerializer
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Rule
import org.junit.Test
import org.junit.runner.RunWith
import org.robolectric.ParameterizedRobolectricTestRunner
import org.robolectric.RuntimeEnvironment
import org.robolectric.annotation.Config
import org.robolectric.annotation.GraphicsMode
import java.time.ZonedDateTime

/**
 * settings.account_health on the phone: each mail account's state in the server's words
 * (GET /connectors `health`), the one line over them as on the web, and a tap that opens the web's
 * Mail accounts page. Light and dark.
 */
@RunWith(ParameterizedRobolectricTestRunner::class)
@GraphicsMode(GraphicsMode.Mode.NATIVE)
@Config(application = Application::class, sdk = [34])
class AccountHealthTest(private val dark: Boolean) {
    companion object {
        @JvmStatic
        @ParameterizedRobolectricTestRunner.Parameters(name = "dark={0}")
        fun params(): List<Array<Any>> = listOf(arrayOf<Any>(false), arrayOf<Any>(true))
    }

    @get:Rule val compose = createComposeRule()
    private val mode get() = if (dark) "dark" else "light"
    private val now = ZonedDateTime.parse("2026-10-07T09:00:00Z")
    private val rel = { _: ZonedDateTime, _: ZonedDateTime -> "5 min ago" }

    private fun acct(id: Long, name: String, state: String, text: String, enabled: Boolean = true) =
        MailAccount(id, "imap", name, enabled, AccountHealth(state, text), "2026-10-07T08:55:00Z")

    // The shape GET /connectors answers (api/connector_routes.py connector_out), with a calendar and Speakr left out.
    @Test fun settingsAccountHealthReadsTheServersWords() {
        val json = """[{"id":1,"kind":"imap","name":"Home","enabled":true,"status":"idle","mode":"store",
            "health":{"state":"ok","text":"Up to date"},"last_sync_at":"2026-10-07T08:55:00+00:00","config":{}},
            {"id":2,"kind":"caldav","name":"Calendar","enabled":true,"health":{"state":"ok","text":"Up to date"}},
            {"id":3,"kind":"speakr","name":"Speakr","enabled":true},
            {"id":4,"kind":"gmail","name":"Work","enabled":true,"health":{"state":"signin","text":"Password required"}}]"""
        val all = MailrJson.decodeFromString(ListSerializer(MailAccount.serializer()), json)
        assertEquals(listOf("Home", "Work"), all.filter { it.isMail() }.map { it.name })
        assertEquals("Password required", all.last().health?.text)
    }

    @Test fun theSummaryLineMatchesTheWeb() {
        assertNull(AccountHealthText.summary(emptyList(), now, rel))
        assertEquals(AccountHealth("ok", "Your account is syncing. Last synced 5 min ago."),
            AccountHealthText.summary(listOf(acct(1, "Home", "ok", "Up to date")), now, rel))
        assertEquals("All 2 accounts are syncing. Last synced 5 min ago.",
            AccountHealthText.summary(listOf(acct(1, "Home", "ok", "Up to date"), acct(2, "Work", "syncing", "Syncing")), now, rel)?.text)
        // A sign-in problem comes first; the others are counted; a paused account is left out.
        val s = AccountHealthText.summary(listOf(acct(1, "Home", "error", "Sync failed. It is tried again automatically."),
            acct(2, "Work", "signin", "Password required"), acct(3, "Old", "stale", "Not synced for 3 hours"),
            acct(4, "Off", "signin", "Password required", enabled = false)), now, rel)
        assertEquals(AccountHealth("signin", "Work: Password required. 2 more to check."), s)
        assertEquals("Home: Sync failed. It is tried again automatically.",
            AccountHealthText.summary(listOf(acct(1, "Home", "error", "Sync failed. It is tried again automatically.")), now, rel)?.text)
    }

    @Test fun eachAccountShowsItsStateAndATapOpensTheWeb() {
        RuntimeEnvironment.setQualifiers("w411dp-h891dp-port-${if (dark) "night" else "notnight"}-xxhdpi")
        var opened = 0
        val accounts = listOf(acct(1, "Home (example.com)", "ok", "Up to date"),
            acct(2, "Work", "signin", "Password required"),
            acct(3, "Newsletters", "stale", "Not synced for 3 hours"),
            acct(4, "Archive import", "paused", "Paused", enabled = false))
        compose.setContent {
            MailrTheme(if (dark) ThemeMode.DARK else ThemeMode.LIGHT, dynamic = false) {
                Surface(Modifier.fillMaxSize(), color = MaterialTheme.colorScheme.surface) {
                    LazyColumn { item { Header("Mail") }; accountItems(accounts) { opened++ } }
                }
            }
        }
        compose.onNodeWithTag("account-health-line").assertExists()
        compose.onNodeWithText("Work: Password required. 1 more to check.").assertExists()
        compose.onNodeWithText("Not synced for 3 hours").assertExists()
        compose.onNodeWithText("Paused").assertExists()
        compose.onRoot().captureRoboImage("screenshots/settings-account-health-$mode.png")
        compose.onNodeWithTag("account-2").performClick()
        assertEquals(1, opened)
    }
}
