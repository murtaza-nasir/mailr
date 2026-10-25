package io.github.murtazanasir.mailr

import android.app.Application
import android.content.Context
import android.content.Intent
import android.net.Uri
import android.os.Looper
import android.os.SystemClock
import android.view.InputDevice
import android.view.MotionEvent
import android.view.View
import android.view.WindowManager
import androidx.activity.ComponentActivity
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.SnackbarHostState
import androidx.compose.ui.test.junit4.createAndroidComposeRule
import androidx.compose.ui.test.onAllNodesWithText
import androidx.compose.ui.test.onNodeWithTag
import androidx.compose.ui.test.onNodeWithText
import androidx.compose.ui.test.performClick
import androidx.test.core.app.ApplicationProvider
import io.github.murtazanasir.mailr.auth.AuthRedirectActivity
import io.github.murtazanasir.mailr.data.api.ApiClient
import io.github.murtazanasir.mailr.data.api.Approval
import io.github.murtazanasir.mailr.data.api.ApprovalPreview
import io.github.murtazanasir.mailr.data.api.AuthApi
import io.github.murtazanasir.mailr.data.api.ComposePayload
import io.github.murtazanasir.mailr.data.auth.AuthManager
import io.github.murtazanasir.mailr.data.auth.NonceBinding
import io.github.murtazanasir.mailr.data.auth.PendingAuthorization
import io.github.murtazanasir.mailr.data.auth.SessionStore
import io.github.murtazanasir.mailr.data.repo.PendingScopes
import io.github.murtazanasir.mailr.testing.effectsOnMainLooper
import io.github.murtazanasir.mailr.testing.eventually
import io.github.murtazanasir.mailr.ui.approvals.ApprovalsScreen
import io.github.murtazanasir.mailr.ui.components.SecureScreens
import io.github.murtazanasir.mailr.ui.onboarding.MailrBrand
import io.github.murtazanasir.mailr.ui.onboarding.OnboardingCallbacks
import io.github.murtazanasir.mailr.ui.onboarding.OnboardingScreen
import io.github.murtazanasir.mailr.ui.onboarding.OnboardingViewModel
import io.github.murtazanasir.mailr.ui.onboarding.parseSetupServer
import kotlinx.coroutines.runBlocking
import kotlinx.serialization.json.Json
import kotlinx.serialization.json.jsonArray
import kotlinx.serialization.json.jsonObject
import kotlinx.serialization.json.jsonPrimitive
import net.openid.appauth.AuthorizationException
import net.openid.appauth.AuthorizationRequest
import net.openid.appauth.AuthorizationResponse
import net.openid.appauth.AuthorizationServiceConfiguration
import net.openid.appauth.ResponseTypeValues
import okhttp3.mockwebserver.Dispatcher
import okhttp3.mockwebserver.MockResponse
import okhttp3.mockwebserver.MockWebServer
import okhttp3.mockwebserver.RecordedRequest
import org.junit.After
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNotNull
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Assert.fail
import org.junit.Rule
import org.junit.Test
import org.junit.runner.RunWith
import org.robolectric.Robolectric
import org.robolectric.RobolectricTestRunner
import org.robolectric.Shadows.shadowOf
import org.robolectric.annotation.Config
import java.io.File
import java.net.URLEncoder
import java.util.concurrent.CopyOnWriteArrayList

/**
 * Android hardening: the regression tests of the security fixes. Each one is a proof of the
 * hole turned around: it fails if the hole opens again.
 */
@RunWith(RobolectricTestRunner::class)
@Config(application = Application::class, sdk = [34], qualifiers = "w411dp-h891dp")
class AndroidHardeningTest {
    @get:Rule(order = Int.MIN_VALUE) val graphs = TestGraph()
    @get:Rule val compose = createAndroidComposeRule<ComponentActivity>().effectsOnMainLooper()
    private val app: Application = ApplicationProvider.getApplicationContext()
    private val servers = mutableListOf<MockWebServer>()

    @After fun tearDown() {
        servers.forEach { runCatching { it.shutdown() } }
    }

    private fun server(seen: MutableList<RecordedRequest>, answer: (RecordedRequest) -> MockResponse?) = MockWebServer().apply {
        dispatcher = object : Dispatcher() {
            override fun dispatch(request: RecordedRequest): MockResponse {
                seen += request
                return answer(request) ?: MockResponse().setResponseCode(404).setBody("""{"detail":"not here"}""")
            }
        }
        start()
        servers += this
    }

    private fun origin(s: MockWebServer) = s.url("/").toString().trimEnd('/')

    private val idp = """{"enabled":true,"issuer":"https://id.example.com","client_id":"mailr-android",
        "redirect_uri":"io.github.murtazanasir.mailr:/oauth2redirect","scopes":["openid","email","profile"],"local_login":false}"""

    // ---------------- A setup link never switches the server by itself ----------------

    @Test fun aSetupLinkWaitsForConfirmationAndNeverTouchesTheOtherServer() {
        val realSeen = CopyOnWriteArrayList<RecordedRequest>()
        val real = server(realSeen) { r -> if (r.path!!.startsWith("/api/v1/auth/native-config")) MockResponse().setBody(idp) else null }
        val evilSeen = CopyOnWriteArrayList<RecordedRequest>()
        val evil = server(evilSeen) { r -> if (r.path!!.startsWith("/api/v1/auth/native-config")) MockResponse().setBody(idp) else null }
        val sessions = SessionStore(app)
        val auth = AuthManager(app, AuthApi(ApiClient(sessions)), sessions)
        val vm = graphs.vm { OnboardingViewModel(sessions, auth) }
        // The user typed and checked their own server.
        vm.setServer(origin(real))
        vm.connect()
        eventually("real config") { vm.state.value.config != null && !vm.state.value.checking }

        val link = "mailr://setup?server=" + URLEncoder.encode(origin(evil), "UTF-8")
        vm.offerSetup(parseSetupServer(link)!!)
        shadowOf(Looper.getMainLooper()).idle()
        // Nothing changed: the server, the checked config, and nothing was asked of the other host.
        assertEquals(origin(real), sessions.server)
        assertEquals(origin(real), vm.state.value.server)
        assertNotNull(vm.state.value.config)
        assertEquals(origin(evil), vm.state.value.setupOffer)
        assertTrue(evilSeen.isEmpty())
        // Cancel: as before.
        vm.dismissSetup()
        assertNull(vm.state.value.setupOffer)
        assertEquals(origin(real), sessions.server)
        // Only the person's tap uses the link's server.
        vm.offerSetup(origin(evil))
        vm.confirmSetup()
        eventually("evil config") { vm.state.value.config != null && !vm.state.value.checking }
        assertEquals(origin(evil), sessions.server)
    }

    @Test fun theConfirmationShowsTheFullHost() {
        var used = 0
        var cancelled = 0
        compose.setContent {
            MaterialTheme {
                OnboardingScreen(io.github.murtazanasir.mailr.ui.onboarding.OnboardingState("https://mail.example.com",
                    setupOffer = "https://mail-example.evil.example:8443"),
                    OnboardingCallbacks({}, {}, {}, {}, { _, _ -> }, confirmSetup = { used++ }, dismissSetup = { cancelled++ }), MailrBrand)
            }
        }
        compose.onNodeWithText("Use this server?").assertExists()
        compose.onNodeWithTag("setup-link-host").assertExists()
        compose.onNodeWithText("mail-example.evil.example:8443").assertExists()
        compose.onNodeWithText("Continue only if this is your own server", substring = true).assertExists()
        compose.onNodeWithText("Cancel").performClick()
        assertEquals(1, cancelled)
        assertEquals(0, used)
        compose.onNodeWithTag("setup-link-use").performClick()
        assertEquals(1, used)
    }

    /** "Use this server" drops a touch that reaches it through another app's overlay. */
    @Test fun theSetupLinkConfirmationRefusesATouchThroughAnOverlay() {
        var used = 0
        compose.setContent {
            MaterialTheme {
                OnboardingScreen(io.github.murtazanasir.mailr.ui.onboarding.OnboardingState("https://mail.example.com",
                    setupOffer = "https://mail-example.evil.example"),
                    OnboardingCallbacks({}, {}, {}, {}, { _, _ -> }, confirmSetup = { used++ }, dismissSetup = {}), MailrBrand)
            }
        }
        val c = compose.onNodeWithTag("setup-link-use").fetchSemanticsNode().boundsInWindow.center
        val root = compose.activity.window.decorView
        compose.runOnIdle {
            root.dispatchTouchEvent(touch(c.x, c.y, MotionEvent.ACTION_DOWN))
            root.dispatchTouchEvent(touch(c.x, c.y, MotionEvent.ACTION_UP))
        }
        compose.waitForIdle()
        assertEquals(0, used)
        compose.runOnIdle {
            root.dispatchTouchEvent(touch(c.x, c.y, MotionEvent.ACTION_DOWN, obscured = false))
            root.dispatchTouchEvent(touch(c.x, c.y, MotionEvent.ACTION_UP, obscured = false))
        }
        compose.waitForIdle()
        assertEquals(1, used)
    }

    /** Every button of a new-mail notification needs the phone unlocked, Archive and Mark read too. */
    @Test fun newMailNotificationActionsNeedTheUnlockedPhone() {
        val actions = io.github.murtazanasir.mailr.data.sync.MailNotifications.actions(ApplicationProvider.getApplicationContext(), 7L)
        assertEquals(3, actions.size)
        actions.forEach { assertTrue("${it.title} works from the lock screen", it.isAuthenticationRequired) }
    }

    /** The nonce formula and the origin spelling, against the server's own cases (backend native_auth). */
    @Test fun nonceMatchesTheServersCases() {
        val text = AndroidHardeningTest::class.java.classLoader!!.getResource("native_nonce_cases.json")!!.readText()
        val cases = Json.parseToJsonElement(text).jsonObject
        cases["origins"]!!.jsonArray.forEach {
            val c = it.jsonObject
            assertEquals(c["in"]!!.jsonPrimitive.content, c["out"]!!.jsonPrimitive.content, NonceBinding.canonicalOrigin(c["in"]!!.jsonPrimitive.content))
        }
        cases["nonces"]!!.jsonArray.forEach {
            val c = it.jsonObject
            assertEquals(c["nonce"]!!.jsonPrimitive.content,
                NonceBinding.nonce(c["origin"]!!.jsonPrimitive.content, c["seed"]!!.jsonPrimitive.content))
        }
        assertTrue(NonceBinding.newSeed().length >= 22)
    }

    private fun request(origin: String, seed: String): AuthorizationRequest {
        val cfg = AuthorizationServiceConfiguration(Uri.parse("https://id.example.com/authorize"), Uri.parse("https://id.example.com/token"))
        return AuthorizationRequest.Builder(cfg, "mailr-android", ResponseTypeValues.CODE, Uri.parse("io.github.murtazanasir.mailr:/oauth2redirect"))
            .setNonce(NonceBinding.nonce(origin, seed)).build()
    }

    @Test fun anAnswerForAnotherServerIsNeverPostedAnywhere() {
        val evilSeen = CopyOnWriteArrayList<RecordedRequest>()
        val evil = server(evilSeen) { null }
        val sessions = SessionStore(app).apply { server = "https://mail.example.com" }
        val seed = NonceBinding.newSeed()
        val req = request("https://mail.example.com", seed)
        val pending = PendingAuthorization(app)
        pending.begin(PendingAuthorization.Pending(req.state!!, seed, "https://mail.example.com", "io.github.murtazanasir.mailr:/oauth2redirect",
            System.currentTimeMillis()))  // clock-ok: the sign-in's age
        // The server changed while the browser was open.
        sessions.server = origin(evil)
        val answer = AuthorizationResponse.Builder(req).setState(req.state).setAuthorizationCode("code-1").build().toIntent()
        val auth = AuthManager(app, AuthApi(ApiClient(sessions)), sessions)
        val err = runCatching { runBlocking { auth.complete(answer) } }.exceptionOrNull()
        assertEquals("The server changed during sign-in. Sign in again.", err?.message)
        assertTrue(evilSeen.isEmpty())
        assertNull(pending.current())
        // An answer to no sign-in this app started (none in progress) is refused too.
        sessions.server = "https://mail.example.com"
        val stray = runCatching { runBlocking { auth.complete(answer) } }.exceptionOrNull()
        assertEquals("Sign-in failed.", stray?.message)
    }

    @Test fun theSeedGoesWithTheIdToken() {
        val seen = CopyOnWriteArrayList<RecordedRequest>()
        val s = server(seen) { r ->
            if (r.path == "/api/v1/auth/oidc/native") MockResponse().setBody(
                """{"session_token":"x","csrf":"y","user":{"id":1,"email":"me@example.com","name":"Me"}}""") else null
        }
        val sessions = SessionStore(app).apply { server = origin(s) }
        runBlocking { AuthApi(ApiClient(sessions)).nativeSignIn("eyJhbGciOiJSUzI1NiJ9.ID.sig", "Pixel 9", null, "seed-123") }
        val body = Json.parseToJsonElement(seen.first { it.path == "/api/v1/auth/oidc/native" }.body.readUtf8()).jsonObject
        assertEquals("seed-123", body["nonce_seed"]!!.jsonPrimitive.content)
    }

    // ---------------- "Allow all n" refuses touches through overlays ----------------

    private fun label(id: Long, name: String, at: String) =
        Approval(id, "token:7", "claude-code", "messages.send", name, ApprovalPreview("Send “$name” to billing@evil.example"), createdAt = at)

    private val pendingApprovals = listOf(label(53, "Invoice C", "2026-10-06T10:03:00Z"), label(52, "Invoice B", "2026-10-06T10:02:00Z"),
        label(51, "Invoice A", "2026-10-06T10:01:00Z"))

    @Suppress("UNCHECKED_CAST")
    private fun windows(): List<Pair<View, WindowManager.LayoutParams>> {
        val wmg = Class.forName("android.view.WindowManagerGlobal").getMethod("getInstance").invoke(null)!!
        fun <T> field(n: String) = wmg.javaClass.getDeclaredField(n).apply { isAccessible = true }.get(wmg) as List<T>
        return field<View>("mViews").zip(field<WindowManager.LayoutParams>("mParams"))
    }

    private fun touch(x: Float, y: Float, action: Int, obscured: Boolean = true): MotionEvent {
        val props = arrayOf(MotionEvent.PointerProperties().apply { id = 0; toolType = MotionEvent.TOOL_TYPE_FINGER })
        val coords = arrayOf(MotionEvent.PointerCoords().apply { this.x = x; this.y = y; pressure = 1f; size = 1f })
        val t = SystemClock.uptimeMillis()
        return MotionEvent.obtain(t, t, action, 1, props, coords, 0, 0, 1f, 1f, 0, 0, InputDevice.SOURCE_TOUCHSCREEN,
            if (obscured) MotionEvent.FLAG_WINDOW_IS_OBSCURED else 0)
    }

    private fun showApprovals(decided: MutableList<List<Long>>) {
        SecureScreens.configure(hideAll = false, hideSensitive = true, activity = null)
        compose.setContent {
            MaterialTheme {
                ApprovalsScreen(pendingApprovals, emptyList(), null, false, SnackbarHostState(), {}, {}, { _, _, _ -> },
                    onDecideMany = { g -> decided += g.map { it.id } })
            }
        }
    }

    @Test fun allowAllConfirmRefusesATouchThroughAnOverlay() {
        val decided = mutableListOf<List<Long>>()
        showApprovals(decided)
        compose.onNodeWithText("Allow all 3").performClick()
        compose.onNodeWithText("Allow each of these once?").assertExists()
        compose.waitForIdle()
        val confirm = compose.onNodeWithTag("allow-all-confirm").fetchSemanticsNode()
        val c = confirm.boundsInWindow.center
        val dialog = windows().last().first
        compose.runOnIdle {
            dialog.dispatchTouchEvent(touch(c.x, c.y, MotionEvent.ACTION_DOWN))
            dialog.dispatchTouchEvent(touch(c.x, c.y, MotionEvent.ACTION_UP))
        }
        compose.waitForIdle()
        assertTrue("an obscured touch approved the group", decided.isEmpty())
        // The dialog is still open; an ordinary tap approves all three.
        compose.onNodeWithTag("allow-all-confirm").performClick()
        compose.waitForIdle()
        assertEquals(listOf(listOf(51L, 52L, 53L)), decided)
    }

    @Test fun allowAllRowRefusesATouchThroughAnOverlay() {
        showApprovals(mutableListOf())
        val row = compose.onAllNodesWithText("Allow all 3")[0].fetchSemanticsNode()
        val c = row.boundsInWindow.center
        val root = compose.activity.window.decorView
        compose.runOnIdle {
            root.dispatchTouchEvent(touch(c.x, c.y, MotionEvent.ACTION_DOWN))
            root.dispatchTouchEvent(touch(c.x, c.y, MotionEvent.ACTION_UP))
        }
        compose.waitForIdle()
        compose.onNodeWithText("Allow each of these once?").assertDoesNotExist()
    }

    /** Every button that decides several approvals at once carries the overlay guard. */
    @Test fun everyAllowAllButtonIsGuarded() {
        val src = File("src/main/java/io/github/murtazanasir/mailr/ui/approvals/ApprovalsUi.kt").readText()
        val buttons = Regex("""(TextButton|FilledTonalButton|Button)\(onClick = [^\n]*(onDecideMany|onAllow)[^\n]*(\n[^\n]*){0,1}""").findAll(src).toList()
        assertTrue(buttons.size >= 2)
        buttons.forEach { assertTrue("not guarded: ${it.value}", "refuseObscuredTouches()" in it.value) }
    }

    // ---------------- The kept queue stays with its server ----------------

    @Test fun keptQueueAndDraftsAreDroppedForAnotherServerWithTheSameAddress() {
        val realSeen = CopyOnWriteArrayList<RecordedRequest>()
        val real = server(realSeen) { null }
        val evilSeen = CopyOnWriteArrayList<RecordedRequest>()
        val evil = server(evilSeen) { r ->
            if (r.path == "/api/v1/conversations/actions") MockResponse().setBody("""{"ok":true,"affected":1}""") else null
        }
        SessionStore(app).apply { server = origin(real); save("tok", "csrf", "me@example.com", "Me") }
        val g = graphs.graph(app)
        runBlocking {
            g.mail.enqueue("POST", "conversations/actions", """{"ids":[7],"action":"archive"}""", "k-archive-7", PendingScopes.describe("archive", 1))
            g.drafts.write("d1", ComposePayload(subject = "Offline: the board minutes"), null)
        }
        g.sessionExpired()
        eventually("expiry wipe") { runBlocking { g.db.pending().next(50).size == 1 } }
        g.sessions.server = origin(evil)
        g.sessions.save("evil-tok", "evil-csrf", "me@example.com", "Me")
        runBlocking { g.adoptKeptQueue("me@example.com") }
        assertTrue(runBlocking { g.db.pending().next(50) }.isEmpty())
        assertNull(runBlocking { g.db.localDrafts().get("d1") })
        runBlocking { g.mail.drain() }
        assertTrue("the other server received the change", evilSeen.none { it.path == "/api/v1/conversations/actions" })
        graphs.close()
    }

    private fun sha256(s: String) = java.security.MessageDigest.getInstance("SHA-256").digest(s.toByteArray()).joinToString("") { "%02x".format(it) }

    /** A queue kept by the older version (address only) becomes the server's that the phone names at start. */
    @Test fun anOlderKeptQueueIsMigratedToItsServer() {
        SessionStore(app).apply { server = "https://mail.example.com" }
        app.getSharedPreferences("mailr_queue", Context.MODE_PRIVATE).edit().putString("owner", sha256("me@example.com")).commit()
        val g = graphs.graph(app)
        runBlocking { g.mail.enqueue("POST", "conversations/actions", """{"ids":[7],"action":"archive"}""", "k-7", PendingScopes.describe("archive", 1)) }
        val prefs = app.getSharedPreferences("mailr_queue", Context.MODE_PRIVATE)
        assertNull(prefs.getString("owner", null))
        assertEquals(sha256("https://mail.example.com\n" + sha256("me@example.com")), prefs.getString("owner_v2", null))
        // The same account on the same server keeps it.
        g.sessions.save("tok", "csrf", "Me@Example.com", "Me")
        runBlocking { g.adoptKeptQueue("Me@Example.com") }
        assertEquals(1, runBlocking { g.db.pending().next(50) }.size)
        graphs.close()
    }

    @Test fun anOlderKeptQueueIsDroppedForAnotherServer() {
        SessionStore(app).apply { server = "https://mail.example.com" }
        app.getSharedPreferences("mailr_queue", Context.MODE_PRIVATE).edit().putString("owner", sha256("me@example.com")).commit()
        val g = graphs.graph(app)
        runBlocking { g.mail.enqueue("POST", "conversations/actions", """{"ids":[7],"action":"archive"}""", "k-7", PendingScopes.describe("archive", 1)) }
        g.sessions.server = "https://mail-example.evil.example"
        g.sessions.save("tok", "csrf", "me@example.com", "Me")
        runBlocking { g.adoptKeptQueue("me@example.com") }
        assertTrue(runBlocking { g.db.pending().next(50) }.isEmpty())
        graphs.close()
    }

    // ---------------- Only the answer to this app's own sign-in gets through ----------------

    private val crafted = Uri.parse("io.github.murtazanasir.mailr:/oauth2redirect?error=server_error&error_description=" +
        Uri.encode("This server has moved. Sign in at https://mail-example.evil.example"))

    private fun redirect(uri: Uri): Intent? =
        shadowOf(Robolectric.buildActivity(AuthRedirectActivity::class.java, Intent(Intent.ACTION_VIEW, uri)).setup().get()).nextStartedActivity

    @Test fun aStrayOrStatelessAnswerIsDropped() {
        val pending = PendingAuthorization(app)
        pending.clear()
        // No sign-in in progress: nothing is forwarded.
        assertNull(redirect(crafted))
        // A sign-in in progress: an answer without its state, with another state, or on another path is dropped.
        pending.begin(PendingAuthorization.Pending("state-abc", "seed", "https://mail.example.com", "io.github.murtazanasir.mailr:/oauth2redirect",
            System.currentTimeMillis()))  // clock-ok: the sign-in's age
        assertNull(redirect(crafted))
        assertNull(redirect(Uri.parse("$crafted&state=guess")))
        assertNull(redirect(Uri.parse("io.github.murtazanasir.mailr:/elsewhere?code=c&state=state-abc")))
        assertNull(redirect(Uri.parse("other.scheme:/oauth2redirect?code=c&state=state-abc")))
        // The provider's answer with the state is handed on.
        val ok = redirect(Uri.parse("io.github.murtazanasir.mailr:/oauth2redirect?code=c&state=state-abc"))
        assertEquals(net.openid.appauth.AuthorizationManagementActivity::class.java.name, ok!!.component!!.className)
        // An old sign-in is not answered.
        pending.begin(PendingAuthorization.Pending("state-abc", "seed", "https://mail.example.com", "io.github.murtazanasir.mailr:/oauth2redirect",
            System.currentTimeMillis() - PendingAuthorization.MAX_AGE_MS - 1000))  // clock-ok: the sign-in's age
        assertNull(redirect(Uri.parse("io.github.murtazanasir.mailr:/oauth2redirect?code=c&state=state-abc")))
        pending.clear()
    }

    @Test fun errorTextFromTheRedirectIsNeverShown() {
        val sessions = SessionStore(app)
        val auth = AuthManager(app, AuthApi(ApiClient(sessions)), sessions)
        fun shown(uri: Uri) = runCatching { runBlocking { auth.complete(AuthorizationException.fromOAuthRedirect(uri).toIntent()) } }
            .exceptionOrNull()!!.message
        assertEquals("Sign-in failed.", shown(crafted))
        assertEquals("The sign-in service refused the sign-in.",
            shown(Uri.parse("io.github.murtazanasir.mailr:/oauth2redirect?error=access_denied&error_description=Call%20us")))
    }
}
