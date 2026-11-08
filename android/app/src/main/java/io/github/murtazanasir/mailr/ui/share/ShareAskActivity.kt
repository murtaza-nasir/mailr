package io.github.murtazanasir.mailr.ui.share

import android.content.Intent
import android.os.Bundle
import android.widget.Toast
import androidx.activity.ComponentActivity
import androidx.activity.compose.setContent
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Surface
import androidx.compose.material3.Text
import androidx.compose.runtime.getValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.testTag
import androidx.compose.ui.unit.dp
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import androidx.lifecycle.lifecycleScope
import io.github.murtazanasir.mailr.AppGraph
import io.github.murtazanasir.mailr.R
import io.github.murtazanasir.mailr.data.agent.AgentClient
import io.github.murtazanasir.mailr.data.agent.ChatFile
import io.github.murtazanasir.mailr.data.api.userMessage
import io.github.murtazanasir.mailr.data.settings.Prefs
import io.github.murtazanasir.mailr.data.share.ShareParser
import io.github.murtazanasir.mailr.data.share.SharedContent
import io.github.murtazanasir.mailr.i18n.AppText
import io.github.murtazanasir.mailr.i18n.AssistantName
import io.github.murtazanasir.mailr.look.CircularProgressIndicator
import io.github.murtazanasir.mailr.ui.MainActivity
import io.github.murtazanasir.mailr.ui.ask.AskIntents
import io.github.murtazanasir.mailr.ui.ask.AskRequest
import io.github.murtazanasir.mailr.ui.assistant.displayName
import io.github.murtazanasir.mailr.ui.components.followSecurePrefs
import io.github.murtazanasir.mailr.ui.graph
import io.github.murtazanasir.mailr.ui.theme.MailrTheme
import kotlinx.coroutines.launch

/**
 * Share → "Ask Ambrose" (ACT agent.chat_attach @android.share): images and files shared from another
 * app are uploaded for the assistant while this activity holds the read grant (POST /agent/attachments,
 * at most [AgentClient.MAX_FILES]), then the app opens on the Assistant tab with them waiting above the
 * message box and any shared words in it. Nothing goes to a model until the user taps Send; a file the
 * server cannot read is named in a toast and left out.
 */
class ShareAskActivity : ComponentActivity() {
    override fun attachBaseContext(newBase: android.content.Context) {
        super.attachBaseContext(io.github.murtazanasir.mailr.i18n.AppLocales.wrap(newBase))
    }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        followSecurePrefs(graph.settings.prefs)
        val g = graph
        if (g.sessions.session.value == null) {
            Toast.makeText(this, getString(R.string.sharing_sign_in_then_share), Toast.LENGTH_LONG).show()
            startActivity(Intent(this, MainActivity::class.java).addFlags(Intent.FLAG_ACTIVITY_NEW_TASK))
            finish()
            return
        }
        val shared = ShareParser.parse(intent, packageName)
        if (shared == null || shared.isEmpty) {
            finish()
            return
        }
        setContent {
            val prefs by g.settings.prefs.collectAsStateWithLifecycle(Prefs())
            MailrTheme(prefs.theme, prefs.dynamicColor, prefs.webColours(), prefs.webLook) {
                Box(Modifier.fillMaxSize(), contentAlignment = Alignment.Center) {
                    Surface(shape = RoundedCornerShape(20.dp), tonalElevation = 6.dp, shadowElevation = 6.dp,
                        modifier = Modifier.testTag("share-ask-progress")) {
                        Row(Modifier.padding(20.dp), verticalAlignment = Alignment.CenterVertically) {
                            CircularProgressIndicator(Modifier.size(22.dp), strokeWidth = 2.dp)
                            Spacer(Modifier.width(14.dp))
                            Text(AppText.get(R.string.asst_attach_sharing, AssistantName.now()), style = MaterialTheme.typography.bodyLarge)
                        }
                    }
                }
            }
        }
        if (savedInstanceState == null) lifecycleScope.launch { hand(g, shared) }
    }

    private suspend fun hand(g: AppGraph, shared: SharedContent) {
        val files = upload(g, shared)
        val words = listOfNotNull(shared.subject ?: shared.title, shared.text).distinct().joinToString("\n\n").ifBlank { null }
        if (files.isNotEmpty() || words != null) {
            g.pendingAsk.value = AskRequest(null, words, files, send = false)
            startActivity(AskIntents.openAssistant(this))
        }
        finish()
    }

    private suspend fun upload(g: AppGraph, shared: SharedContent): List<ChatFile> {
        if (shared.files.size > AgentClient.MAX_FILES) {
            Toast.makeText(this, AppText.plural(R.plurals.asst_attach_limit, AgentClient.MAX_FILES, AgentClient.MAX_FILES), Toast.LENGTH_LONG).show()
        }
        val out = mutableListOf<ChatFile>()
        for (uri in shared.files.take(AgentClient.MAX_FILES)) {
            try {
                out += g.agent.uploadAttachment(contentResolver, uri)
            } catch (e: kotlinx.coroutines.CancellationException) {
                throw e
            } catch (e: Exception) {
                Toast.makeText(this, AppText.get(R.string.asst_attach_failed, displayName(contentResolver, uri), e.userMessage()),
                    Toast.LENGTH_LONG).show()
            }
        }
        return out
    }
}
