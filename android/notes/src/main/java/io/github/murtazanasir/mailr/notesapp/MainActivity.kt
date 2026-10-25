package io.github.murtazanasir.mailr.notesapp

import io.github.murtazanasir.mailr.notesapp.ui.followHideContent

import androidx.lifecycle.lifecycleScope
import kotlinx.coroutines.flow.drop
import kotlinx.coroutines.launch
import android.content.Intent
import android.os.Bundle
import androidx.activity.ComponentActivity
import androidx.activity.compose.setContent
import androidx.activity.enableEdgeToEdge
import androidx.core.splashscreen.SplashScreen.Companion.installSplashScreen
import io.github.murtazanasir.mailr.notesapp.ui.NotesAppRoot
import kotlinx.coroutines.flow.MutableStateFlow

/**
 * The Notes app's one screen: the grid straight from the phone's copy (the first frame does not
 * wait for the network), a note, a table, a row; sign-in when no one is signed in. Links,
 * launcher shortcuts, widgets and notifications arrive here as `mailr-notes://…` ([DeepLinks]).
 */
class MainActivity : ComponentActivity() {
    // The app's language before Android 13; a no-op from 13 on.
    override fun attachBaseContext(newBase: android.content.Context) {
        super.attachBaseContext(io.github.murtazanasir.mailr.i18n.AppLocales.wrap(newBase))
    }

    private val links = MutableStateFlow<NotesLink?>(null)

    override fun onCreate(savedInstanceState: Bundle?) {
        installSplashScreen()
        enableEdgeToEdge()
        super.onCreate(savedInstanceState)
        // Before Android 13 a new app language applies by recreating the screen (AppLocales.set).
        lifecycleScope.launch {
            io.github.murtazanasir.mailr.i18n.AppLocales.changes.drop(1).collect { recreate() }
        }
        if (savedInstanceState == null) links.value = DeepLinks.parse(intent?.dataString)
        val g = (application as NotesApplication).graph
        followHideContent(g.prefs)
        setContent { NotesAppRoot(g, links) { links.value = null } }
    }

    override fun onNewIntent(intent: Intent) {
        super.onNewIntent(intent)
        DeepLinks.parse(intent.dataString)?.let { links.value = it }
    }
}
