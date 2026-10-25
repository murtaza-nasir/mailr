package io.github.murtazanasir.mailr.notesapp.capture

import io.github.murtazanasir.mailr.notesapp.ui.followHideContent

import androidx.compose.ui.res.stringResource
import io.github.murtazanasir.mailr.notesapp.R
import io.github.murtazanasir.mailr.i18n.AppText
import android.content.Intent
import android.os.Bundle
import android.widget.Toast
import androidx.activity.ComponentActivity
import androidx.activity.compose.setContent
import androidx.activity.enableEdgeToEdge
import androidx.compose.runtime.getValue
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import io.github.murtazanasir.mailr.notesapp.MainActivity
import io.github.murtazanasir.mailr.notesapp.NotesApplication
import io.github.murtazanasir.mailr.notesapp.ui.NotesTheme

/**
 * The share target (surface `android.notesshare`): text and links from any app become a note,
 * items of a list or rows of a table ([ShareSheet]). Images are refused with "Images are not
 * supported yet".
 */
class ShareActivity : ComponentActivity() {
    // The app's language before Android 13; a no-op from 13 on.
    override fun attachBaseContext(newBase: android.content.Context) {
        super.attachBaseContext(io.github.murtazanasir.mailr.i18n.AppLocales.wrap(newBase))
    }

    override fun onCreate(savedInstanceState: Bundle?) {
        enableEdgeToEdge()
        super.onCreate(savedInstanceState)
        val g = (application as NotesApplication).graph
        followHideContent(g.prefs)
        val shared = read(intent)
        setContent {
            val look by g.prefs.look.collectAsStateWithLifecycle()
            val session by g.sessions.session.collectAsStateWithLifecycle()
            NotesTheme(look) {
                when {
                    shared == null -> NoticeSheet(stringResource(R.string.notesapp_nothing_to_save), stringResource(R.string.notesapp_nothing_to_save_hint), onDone = ::finish)
                    shared.image -> NoticeSheet(stringResource(R.string.notesapp_images_unsupported), stringResource(R.string.notesapp_images_unsupported_hint), onDone = ::finish)
                    session == null -> NoticeSheet(stringResource(R.string.notesapp_sign_in_to_notes), stringResource(R.string.notesapp_sign_in_first), stringResource(R.string.notesapp_open_notes),
                        onAction = { startActivity(Intent(this, MainActivity::class.java)); finish() }, onDone = ::finish)
                    else -> ShareSheet(g.notes.repo, g.prefs, shared.text, shared.subject) { msg ->
                        if (msg != null) Toast.makeText(this, msg, Toast.LENGTH_SHORT).show()
                        finish()
                    }
                }
            }
        }
    }

    /** What was shared: text with its subject, or an image (refused). */
    data class Incoming(val text: String, val subject: String?, val image: Boolean = false)

    companion object {
        fun read(intent: Intent?): Incoming? {
            if (intent == null) return null
            if (intent.action != Intent.ACTION_SEND && intent.action != Intent.ACTION_SEND_MULTIPLE) return null
            val type = intent.type.orEmpty()
            if (type.startsWith("image/")) return Incoming("", null, image = true)
            val text = intent.getCharSequenceExtra(Intent.EXTRA_TEXT)?.toString()?.takeIf { it.isNotBlank() } ?: return null
            val subject = (intent.getStringExtra(Intent.EXTRA_SUBJECT) ?: intent.getStringExtra(Intent.EXTRA_TITLE))?.takeIf { it.isNotBlank() }
            return Incoming(text.take(20_000), subject?.take(500))
        }
    }
}
