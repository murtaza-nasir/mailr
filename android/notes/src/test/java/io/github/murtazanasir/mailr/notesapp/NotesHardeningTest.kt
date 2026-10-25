package io.github.murtazanasir.mailr.notesapp

import android.app.Application
import android.content.ComponentName
import android.content.Context
import android.content.Intent
import android.net.Uri
import android.view.WindowManager
import androidx.activity.ComponentActivity
import androidx.test.core.app.ApplicationProvider
import io.github.murtazanasir.mailr.data.auth.Handover
import io.github.murtazanasir.mailr.data.auth.PendingAuthorization
import io.github.murtazanasir.mailr.notesapp.auth.AuthRedirectActivity
import io.github.murtazanasir.mailr.notesapp.data.NotesPrefs
import io.github.murtazanasir.mailr.notesapp.ui.followHideContent
import io.github.murtazanasir.mailr.notesapp.ui.mailrIntent
import io.github.murtazanasir.mailr.notesapp.ui.mailrView
import kotlinx.coroutines.runBlocking
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Rule
import org.junit.Test
import org.junit.runner.RunWith
import org.robolectric.Robolectric
import org.robolectric.RobolectricTestRunner
import org.robolectric.Shadows.shadowOf
import org.robolectric.annotation.Config

/**
 * The Notes app's security fixes: hiding content in Recents and screenshots, and its share of the
 * setup-link, per-server cache and sign-in redirect fixes.
 */
@RunWith(RobolectricTestRunner::class)
@Config(application = Application::class, sdk = [34])
class NotesHardeningTest {
    @get:Rule val graphs = NotesTestGraph()
    private val app: Application = ApplicationProvider.getApplicationContext()

    @Test fun mainActivityHasNoTaskAffinity() {
        val info = app.packageManager.getActivityInfo(ComponentName(app, "io.github.murtazanasir.mailr.notesapp.MainActivity"), 0)
        assertEquals("", info.taskAffinity.orEmpty())
    }

    /** The sign-in answer comes through the app's own redirect activity; AppAuth's receiver is not exported. */
    @Test fun theRedirectReceiverIsTheCheckedOne() {
        val stock = app.packageManager.getActivityInfo(ComponentName(app, "net.openid.appauth.RedirectUriReceiverActivity"), 0)
        assertFalse(stock.exported)
        val ours = app.packageManager.getActivityInfo(ComponentName(app, AuthRedirectActivity::class.java), 0)
        assertTrue(ours.exported)
        val pending = PendingAuthorization(app)
        pending.clear()
        fun forward(uri: String): Intent? = shadowOf(Robolectric.buildActivity(AuthRedirectActivity::class.java,
            Intent(Intent.ACTION_VIEW, Uri.parse(uri))).setup().get()).nextStartedActivity
        val crafted = "io.github.murtazanasir.mailr.notes:/oauth2redirect?error=server_error&error_description=Moved"
        assertNull(forward(crafted))
        pending.begin(PendingAuthorization.Pending("st-1", "seed", "https://mail.example.com", "io.github.murtazanasir.mailr.notes:/oauth2redirect",
            System.currentTimeMillis()))  // clock-ok: the sign-in's age
        assertNull(forward(crafted))
        assertTrue(forward("io.github.murtazanasir.mailr.notes:/oauth2redirect?code=c&state=st-1") != null)
        pending.clear()
    }

    @Test fun hideContentSetsFlagSecure() {
        val prefs = NotesPrefs(app)
        prefs.setHideContent(false)
        val a = Robolectric.buildActivity(ComponentActivity::class.java).setup().get()
        a.followHideContent(prefs)
        fun secure() = a.window.attributes.flags and WindowManager.LayoutParams.FLAG_SECURE != 0
        assertFalse(secure())
        prefs.setHideContent(true)
        shadowOf(android.os.Looper.getMainLooper()).idle()
        assertTrue(secure())
        assertTrue(NotesPrefs(app).hideContent.value)  // kept
        prefs.setHideContent(false)
        shadowOf(android.os.Looper.getMainLooper()).idle()
        assertFalse(secure())
    }

    @Test fun openInMailrGoesToTheMailrAppOnly() {
        val mailr = Handover.mailrPackageFor(app.packageName)
        val i = mailrIntent(app, "note", "n1")
        assertEquals("mailr://notes/n/n1", i.data.toString())
        assertEquals(mailr, i.`package`)
        assertEquals(mailr, mailrView(app, "mailr://event/abc").`package`)
    }

    /** The cache and unsent changes belong to the server and the account, not the address alone. */
    @Test fun anotherServerWithTheSameAddressStartsEmpty() {
        val g = graphs.graph(app)
        g.sessions.server = "https://mail.example.com"
        runBlocking { g.adoptCache("me@example.com") }
        g.prefs.opened("note", "n1")
        runBlocking { g.adoptCache("ME@example.com") }
        assertEquals(listOf("note:n1"), g.prefs.recents())  // same account, same server: kept
        g.sessions.server = "https://mail-example.evil.example"
        runBlocking { g.adoptCache("me@example.com") }
        assertTrue(g.prefs.recents().isEmpty())
    }

    @Test fun anOlderOwnerIsMigratedToTheServerAtStart() {
        io.github.murtazanasir.mailr.data.auth.SessionStore(app).server = "https://mail.example.com"
        fun sha(s: String) = java.security.MessageDigest.getInstance("SHA-256").digest(s.toByteArray()).joinToString("") { "%02x".format(it) }
        app.getSharedPreferences("notes_owner", Context.MODE_PRIVATE).edit().putString("owner", sha("me@example.com")).commit()
        val g = graphs.graph(app)
        val prefs = app.getSharedPreferences("notes_owner", Context.MODE_PRIVATE)
        assertNull(prefs.getString("owner", null))
        assertEquals(sha("https://mail.example.com\n" + sha("me@example.com")), prefs.getString("owner_v2", null))
        g.prefs.opened("note", "n1")
        runBlocking { g.adoptCache("me@example.com") }
        assertEquals(listOf("note:n1"), g.prefs.recents())
    }
}
