package io.github.murtazanasir.mailr.notesapp

import android.app.Application
import android.content.Intent
import android.content.pm.PackageManager
import androidx.compose.runtime.getValue
import androidx.compose.runtime.setValue
import androidx.compose.ui.test.assertIsDisplayed
import androidx.compose.ui.test.onNodeWithTag
import androidx.compose.ui.test.onNodeWithText
import androidx.compose.ui.test.performClick
import androidx.test.core.app.ApplicationProvider
import io.github.murtazanasir.mailr.data.auth.Handover
import io.github.murtazanasir.mailr.notesapp.auth.MailrHandover
import io.github.murtazanasir.mailr.notesapp.ui.NotesBrand
import io.github.murtazanasir.mailr.testing.createComposeRule
import io.github.murtazanasir.mailr.testing.eventually
import io.github.murtazanasir.mailr.ui.onboarding.HandoverAccount
import io.github.murtazanasir.mailr.ui.onboarding.HandoverSource
import io.github.murtazanasir.mailr.ui.onboarding.OnboardingCallbacks
import io.github.murtazanasir.mailr.ui.onboarding.OnboardingScreen
import io.github.murtazanasir.mailr.ui.onboarding.OnboardingState
import io.github.murtazanasir.mailr.ui.onboarding.OnboardingViewModel
import kotlinx.serialization.json.Json
import kotlinx.serialization.json.jsonObject
import kotlinx.serialization.json.jsonPrimitive
import okhttp3.mockwebserver.MockResponse
import okhttp3.mockwebserver.MockWebServer
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Rule
import org.junit.Test
import org.junit.runner.RunWith
import org.robolectric.RobolectricTestRunner
import org.robolectric.annotation.Config

/**
 * "Continue as <name>" (session-core Handover): the Notes app offers it when the mailr app on the
 * phone is signed in on the same server, asks the mailr app with a PKCE challenge, and redeems the
 * token it gets back with the verifier for a session of its own.
 */
@RunWith(RobolectricTestRunner::class)
@Config(application = Application::class, sdk = [34], qualifiers = "w411dp-h891dp")
class ContinueAsTest {
    @get:Rule(order = Int.MIN_VALUE) val graphs = NotesTestGraph()
    @get:Rule val compose = createComposeRule()
    private val ctx = ApplicationProvider.getApplicationContext<Application>()

    private class FakeMailr(var account: HandoverAccount?) : HandoverSource {
        val asked = mutableListOf<Pair<String, String>>()
        override fun account() = account
        override fun intent(challenge: String, device: String): Intent {
            asked += challenge to device
            return Intent("test.handover")
        }
    }

    @Test fun theOfferShowsForTheSameServerOnly() {
        var tapped = 0
        val sam = HandoverAccount("https://mail.example.com", "Sam Example", "sam@example.com")
        var state by androidx.compose.runtime.mutableStateOf(OnboardingState("https://mail.example.com", account = sam))
        compose.setContent {
            OnboardingScreen(state, OnboardingCallbacks({}, {}, {}, {}, { _, _ -> }, continueAs = { tapped++ }), NotesBrand)
        }
        compose.onNodeWithText("Continue as Sam Example").assertIsDisplayed()
        compose.onNodeWithText("Signed in to Mailr on this phone · mail.example.com").assertIsDisplayed()
        compose.onNodeWithTag("continue-as").performClick()
        assertEquals(1, tapped)
        // An empty server field: the mailr app's server is offered too.
        state = OnboardingState("", account = sam)
        compose.onNodeWithText("Continue as Sam Example").assertIsDisplayed()
        // Another server: no offer.
        state = OnboardingState("https://other.example.org", account = sam)
        compose.waitForIdle()
        assertTrue(compose.onAllNodes(androidx.compose.ui.test.hasTestTag("continue-as")).fetchSemanticsNodes().isEmpty())
    }

    @Test fun theTokenAndTheVerifierOpenASessionOfItsOwn() {
        val srv = graphs.closeLast(MockWebServer().apply {
            enqueue(MockResponse().setBody("""{"session_token": "notes-session", "csrf": "c-1",
                "user": {"id": 7, "email": "sam@example.com", "name": "Sam Example"}, "expires_at": "2026-11-05T00:00:00Z"}"""))
            start()
        })
        val server = srv.url("/").toString().trimEnd('/')
        val g = graphs.graph(ctx)
        g.sessions.server = ""
        val mailr = FakeMailr(HandoverAccount(server, "Sam Example", "sam@example.com"))
        val vm = graphs.vm { OnboardingViewModel(g.sessions, g.auth, mailr, deviceName = { "Notes on Pixel 9" }) }
        vm.refreshHandover()
        compose.eventually("the mailr app's account") { vm.state.value.continueAs != null }
        vm.continueAs()
        val (challenge, device) = mailr.asked.single()
        assertTrue(Handover.isChallenge(challenge))
        assertEquals("Notes on Pixel 9", device)
        assertTrue(vm.state.value.signingIn)
        vm.finishHandover(true, Intent().putExtra(Handover.EXTRA_TOKEN, "tok-1").putExtra(Handover.EXTRA_SERVER, server))
        compose.eventually("the session") { g.sessions.session.value != null }
        assertEquals("notes-session", g.sessions.session.value!!.token)
        assertEquals(server, g.sessions.server)
        val sent = srv.takeRequest()
        assertEquals("/api/v1/auth/handover/redeem", sent.path)
        val body = Json.parseToJsonElement(sent.body.readUtf8()).jsonObject
        assertEquals("tok-1", body["token"]!!.jsonPrimitive.content)
        assertEquals("the verifier of the challenge sent to the mailr app", challenge, Handover.challengeOf(body["verifier"]!!.jsonPrimitive.content))
    }

    @Test fun aRefusalOrAnotherServerSignsNothingIn() {
        val g = graphs.graph(ctx)
        val mailr = FakeMailr(HandoverAccount("https://mail.example.com", "Sam Example", "sam@example.com"))
        g.sessions.server = "https://mail.example.com"
        val vm = graphs.vm { OnboardingViewModel(g.sessions, g.auth, mailr) }
        vm.refreshHandover()
        compose.eventually("the mailr app's account") { vm.state.value.continueAs != null }
        vm.continueAs()
        vm.finishHandover(false, Intent().putExtra(Handover.EXTRA_ERROR, "The Mailr app is not signed in."))
        assertEquals("The Mailr app is not signed in.", vm.state.value.error)
        vm.continueAs()
        // A token for a server other than the one offered is not redeemed.
        vm.finishHandover(true, Intent().putExtra(Handover.EXTRA_TOKEN, "tok").putExtra(Handover.EXTRA_SERVER, "https://evil.example.net"))
        assertEquals("Sign-in cancelled.", vm.state.value.error)
        // An answer nobody asked for is ignored.
        vm.finishHandover(true, Intent().putExtra(Handover.EXTRA_TOKEN, "tok").putExtra(Handover.EXTRA_SERVER, "https://mail.example.com"))
        compose.waitForIdle()
        assertNull(g.sessions.session.value)
    }

    @Test fun withoutTheMailrAppThereIsNoOffer() {
        assertNull(MailrHandover(ctx).account())
        val i = MailrHandover(ctx).intent("c".repeat(43), "Notes on Pixel 9")
        assertEquals(Handover.mailrPackageFor(ctx.packageName), i.component!!.packageName)
        assertEquals(Handover.ACTIVITY, i.component!!.className)
    }

    @Test fun aMailrAppSignedByAnotherKeyIsNotAsked() {
        val mailr = Handover.mailrPackageFor(ctx.packageName)
        fun install(pkg: String, cert: String) = org.robolectric.Shadows.shadowOf(ctx.packageManager).installPackage(
            android.content.pm.PackageInfo().apply {
                packageName = pkg
                @Suppress("DEPRECATION")
                signatures = arrayOf(android.content.pm.Signature(cert))
            })
        install(ctx.packageName, "0a0b")
        install(mailr, "0c0d")
        assertTrue(!MailrHandover(ctx).trusted())
        assertNull(MailrHandover(ctx).account())
        install(mailr, "0a0b")
        assertTrue(MailrHandover(ctx).trusted())
    }

    @Test fun theManifestAsksForTheMailrAppsPermission() {
        val mailr = Handover.mailrPackageFor(ctx.packageName)
        val info = ctx.packageManager.getPackageInfo(ctx.packageName, PackageManager.GET_PERMISSIONS)
        assertTrue(info.requestedPermissions!!.contains(Handover.permission(mailr)))
        assertTrue(info.permissions!!.any { it.name == Handover.permission(mailr) })
    }
}
