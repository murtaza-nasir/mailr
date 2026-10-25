package io.github.murtazanasir.mailr.ui

import io.github.murtazanasir.mailr.util.catching

import androidx.lifecycle.lifecycleScope
import kotlinx.coroutines.flow.drop
import kotlinx.coroutines.launch
import io.github.murtazanasir.mailr.ui.components.followSecurePrefs
import io.github.murtazanasir.mailr.ui.onboarding.parseSetupInvite
import io.github.murtazanasir.mailr.ui.onboarding.parseSetupServer
import io.github.murtazanasir.mailr.ui.util.InternalLinks
import android.content.Intent
import android.os.Bundle
import androidx.activity.ComponentActivity
import androidx.activity.compose.setContent
import androidx.activity.enableEdgeToEdge
import androidx.core.splashscreen.SplashScreen.Companion.installSplashScreen
import kotlinx.coroutines.flow.MutableStateFlow
import io.github.murtazanasir.mailr.R

class MainActivity : ComponentActivity() {
    // The app's language before Android 13; a no-op from 13 on.
    override fun attachBaseContext(newBase: android.content.Context) {
        super.attachBaseContext(io.github.murtazanasir.mailr.i18n.AppLocales.wrap(newBase))
    }

    private val deepLinks = MutableStateFlow<Destination?>(null)
    /** The server from a setup link (mailr://setup?server=…), for the sign-in screen. */
    private val setupLinks = MutableStateFlow<String?>(null)

    override fun onCreate(savedInstanceState: Bundle?) {
        installSplashScreen()
        enableEdgeToEdge()
        super.onCreate(savedInstanceState)
        // Before Android 13 a new app language applies by recreating the screen (AppLocales.set).
        lifecycleScope.launch {
            io.github.murtazanasir.mailr.i18n.AppLocales.changes.drop(1).collect { recreate() }
        }
        followSecurePrefs(graph.settings.prefs)
        if (savedInstanceState == null) {
            deepLinks.value = parseDeepLink(intent, serverHost(), InternalLinks.verify(this, intent))
            takeSetupLink(intent?.dataString)
        }
        setContent { MailrRoot(deepLinks, { deepLinks.value = null }, setupLinks, { setupLinks.value = null }) }

        // Notification permission is asked in the app (with a rationale) after sign-in; see App.kt.
    }

    override fun onNewIntent(intent: Intent) {
        super.onNewIntent(intent)
        parseDeepLink(intent, serverHost(), InternalLinks.verify(this, intent))?.let { deepLinks.value = it }
        takeSetupLink(intent.dataString)
    }

    /**
     * A setup link (mailr://setup?server=…[&invite=…]) while signed out: the sign-in screen asks before
     * it uses the server, and a household invite in it waits for the sign-in to that server
     * (data/auth/PendingInvite.kt). While signed in it is ignored, with a short notice: it
     * is never kept for a later sign-out.
     */
    private fun takeSetupLink(link: String?) {
        val server = parseSetupServer(link) ?: return
        if (graph.sessions.session.value != null) {
            android.widget.Toast.makeText(this, getString(R.string.ui_setup_link_signed_in), android.widget.Toast.LENGTH_LONG).show()
            return
        }
        setupLinks.value = server
        parseSetupInvite(link)?.let { io.github.murtazanasir.mailr.data.auth.PendingInvite(this).save(server, it) }
    }

    /** The signed-in account's host: https links open only for it. */
    private fun serverHost(): String? = catching { android.net.Uri.parse(graph.sessions.server).host }.getOrNull()
}
