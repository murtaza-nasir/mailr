package io.github.murtazanasir.mailr

import android.app.Activity
import android.app.Application
import android.content.ComponentName
import android.content.Intent
import android.content.pm.PackageInfo
import android.content.pm.PermissionInfo
import android.content.pm.Signature
import androidx.test.core.app.ApplicationProvider
import io.github.murtazanasir.mailr.auth.HandoverActivity
import io.github.murtazanasir.mailr.auth.HandoverMinter
import io.github.murtazanasir.mailr.data.auth.Handover
import kotlinx.coroutines.runBlocking
import kotlinx.serialization.json.Json
import kotlinx.serialization.json.jsonObject
import kotlinx.serialization.json.jsonPrimitive
import okhttp3.mockwebserver.MockResponse
import okhttp3.mockwebserver.MockWebServer
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Rule
import org.junit.Test
import org.junit.runner.RunWith
import org.robolectric.RobolectricTestRunner
import org.robolectric.Shadows.shadowOf
import org.robolectric.annotation.Config

/**
 * The mailr app's half of the Notes app's "Continue as <name>" (session-core Handover): who may ask,
 * what the manifest exports and behind what, and the token it mints with its own session.
 */
@RunWith(RobolectricTestRunner::class)
@Config(application = Application::class, sdk = [34])
class SignInHandoverTest {
    @get:Rule(order = Int.MIN_VALUE) val graphs = TestGraph()
    private val app = ApplicationProvider.getApplicationContext<Application>()
    private val pm get() = app.packageManager
    private val own get() = app.packageName

    @Test fun theChallengeIsPkceS256() {
        // RFC 7636, appendix B.
        assertEquals("E9Melhoa2OwvFrEMTJguCHaoeK1t8URWbuGJSstw-cM", Handover.challengeOf("dBjftJeZ4CVP-mB92K27uhbUJU1p1r_wW1gFWFOEjXk"))
        val v = Handover.newVerifier()
        assertEquals(43, v.length)
        assertTrue(Handover.isChallenge(Handover.challengeOf(v)))
        assertFalse(Handover.isChallenge("short"))
        assertFalse(Handover.isChallenge(null))
    }

    @Test fun eachBuildPairsWithItsOwnNotesApp() {
        assertEquals("io.github.murtazanasir.mailr.notes", Handover.notesPackageFor("io.github.murtazanasir.mailr"))
        assertEquals("io.github.murtazanasir.mailr.notes.debug", Handover.notesPackageFor("io.github.murtazanasir.mailr.debug"))
        assertEquals("io.github.murtazanasir.mailr", Handover.mailrPackageFor("io.github.murtazanasir.mailr.notes"))
        assertEquals("io.github.murtazanasir.mailr.debug", Handover.mailrPackageFor("io.github.murtazanasir.mailr.notes.debug"))
    }

    private fun install(pkg: String, cert: String) {
        shadowOf(pm).installPackage(PackageInfo().apply {
            packageName = pkg
            @Suppress("DEPRECATION")
            signatures = arrayOf(Signature(cert))
        })
    }

    @Test fun onlyTheNotesAppSignedWithTheSameCertificateMayAsk() {
        val ours = "0a0b"
        install(own, ours)
        val notes = Handover.notesPackageFor(own)
        install(notes, ours)
        assertTrue(Handover.callerAllowed(pm, notes, own))
        install("cc.example.other", ours)
        assertFalse("another package, same certificate", Handover.callerAllowed(pm, "cc.example.other", own))
        install(notes, "0c0d")
        assertFalse("the Notes package, another certificate", Handover.callerAllowed(pm, notes, own))
        assertFalse("no caller (not started for a result)", Handover.callerAllowed(pm, null, own))
    }

    @Test fun theActivityAndTheProviderSitBehindTheSignaturePermission() {
        val perm = Handover.permission(own)
        assertEquals(PermissionInfo.PROTECTION_SIGNATURE, pm.getPermissionInfo(perm, 0).protectionLevel and PermissionInfo.PROTECTION_MASK_BASE)
        val a = pm.getActivityInfo(ComponentName(app, HandoverActivity::class.java), 0)
        assertTrue(a.exported)
        assertEquals(perm, a.permission)
        assertEquals(Handover.ACTIVITY, a.name)
        val p = pm.resolveContentProvider(Handover.authority(own), 0)!!
        assertTrue(p.exported)
        assertEquals(perm, p.readPermission)
        assertEquals(perm, p.writePermission)
    }

    private fun server(vararg answers: MockResponse) = graphs.closeLast(MockWebServer().apply { answers.forEach(::enqueue); start() })

    private fun request(challenge: String? = Handover.challengeOf(Handover.newVerifier())) =
        Intent().putExtra(Handover.EXTRA_CHALLENGE, challenge).putExtra(Handover.EXTRA_DEVICE, "Notes on Pixel 9")

    @Test fun aSignedInAppMintsATokenForItsServer() {
        val srv = server(MockResponse().setBody("""{"token": "tok-123", "expires_in": 120}"""))
        val g = graphs.graph(app)
        g.sessions.server = srv.url("/").toString()
        g.sessions.save("session-1", "csrf-1", "sam@example.com", "Sam Example")
        val challenge = Handover.challengeOf(Handover.newVerifier())
        val r = runBlocking { HandoverMinter(g.sessions, g.api.auth).result(true, request(challenge)) }
        assertEquals(Activity.RESULT_OK, r.code)
        assertEquals("tok-123", r.data.getStringExtra(Handover.EXTRA_TOKEN))
        assertEquals(g.sessions.server, r.data.getStringExtra(Handover.EXTRA_SERVER))
        val sent = srv.takeRequest()
        assertEquals("/api/v1/auth/handover", sent.path)
        assertEquals("mailr_session=session-1", sent.getHeader("Cookie"))
        assertEquals("csrf-1", sent.getHeader("X-CSRF-Token"))
        assertNull("no idempotency replay of a token", sent.getHeader("Idempotency-Key"))
        val body = Json.parseToJsonElement(sent.body.readUtf8()).jsonObject
        assertEquals("notes", body["app"]!!.jsonPrimitive.content)
        assertEquals(challenge, body["challenge"]!!.jsonPrimitive.content)
        assertEquals("Notes on Pixel 9", body["device_name"]!!.jsonPrimitive.content)
    }

    @Test fun itRefusesAStrangerABadRequestAndASignedOutApp() {
        val srv = server()
        val g = graphs.graph(app)
        g.sessions.server = srv.url("/").toString()
        val m = HandoverMinter(g.sessions, g.api.auth)
        g.sessions.save("session-1", "csrf-1", "sam@example.com", "Sam Example")
        val stranger = runBlocking { m.result(false, request()) }
        assertEquals(Activity.RESULT_CANCELED, stranger.code)
        assertTrue(stranger.data.getStringExtra(Handover.EXTRA_ERROR)!!.contains("Only the Notes app"))
        assertEquals(Activity.RESULT_CANCELED, runBlocking { m.result(true, request("not-a-challenge")) }.code)
        g.sessions.clear()
        val out = runBlocking { m.result(true, request()) }
        assertEquals(Activity.RESULT_CANCELED, out.code)
        assertTrue(out.data.getStringExtra(Handover.EXTRA_ERROR)!!.contains("not signed in"))
        assertEquals("nothing reached the server", 0, srv.requestCount)
    }
}
