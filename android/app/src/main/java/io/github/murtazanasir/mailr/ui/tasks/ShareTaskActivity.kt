package io.github.murtazanasir.mailr.ui.tasks

import io.github.murtazanasir.mailr.util.catching

import io.github.murtazanasir.mailr.R
import io.github.murtazanasir.mailr.ui.components.followSecurePrefs
import android.content.Intent
import android.os.Bundle
import android.widget.Toast
import androidx.activity.ComponentActivity
import androidx.activity.compose.setContent
import androidx.activity.enableEdgeToEdge
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.safeDrawingPadding
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Surface
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.setValue
import androidx.compose.ui.Modifier
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import io.github.murtazanasir.mailr.AppGraph
import io.github.murtazanasir.mailr.data.api.TaskDraft
import io.github.murtazanasir.mailr.data.api.userMessage
import io.github.murtazanasir.mailr.data.cal.WriteResult
import io.github.murtazanasir.mailr.data.settings.Prefs
import io.github.murtazanasir.mailr.data.share.SelfSubject
import io.github.murtazanasir.mailr.data.share.ShareParser
import io.github.murtazanasir.mailr.data.share.SharedContent
import io.github.murtazanasir.mailr.ui.MainActivity
import io.github.murtazanasir.mailr.ui.calendar.TaskEditor
import io.github.murtazanasir.mailr.ui.calendar.TaskEditorState
import io.github.murtazanasir.mailr.ui.graph
import io.github.murtazanasir.mailr.ui.theme.MailrTheme
import kotlinx.coroutines.launch

/**
 * Share → "New task" (surface android.share): text or a link shared to mailr opens the task
 * editor with the text as the title; a link goes to the notes. It runs in the sharing app's
 * task, so Save (or closing) goes back to where the user shared from.
 */
class ShareTaskActivity : ComponentActivity() {
    // The app's language before Android 13; a no-op from 13 on.
    override fun attachBaseContext(newBase: android.content.Context) {
        super.attachBaseContext(io.github.murtazanasir.mailr.i18n.AppLocales.wrap(newBase))
    }

    override fun onCreate(savedInstanceState: Bundle?) {
        enableEdgeToEdge()
        super.onCreate(savedInstanceState)
        followSecurePrefs(graph.settings.prefs)
        val g = graph
        if (g.sessions.session.value == null) {
            Toast.makeText(this, getString(R.string.tasks_share_sign_in), Toast.LENGTH_LONG).show()
            startActivity(Intent(this, MainActivity::class.java).addFlags(Intent.FLAG_ACTIVITY_NEW_TASK))
            finish()
            return
        }
        val draft = ShareTask.draft(ShareParser.parse(intent, packageName) ?: SharedContent(), g.calendar.newUid())
        setContent {
            val prefs by g.settings.prefs.collectAsStateWithLifecycle(Prefs())
            MailrTheme(prefs.theme, prefs.dynamicColor, prefs.webColours(), prefs.webLook) {
                Surface(Modifier.fillMaxSize(), color = MaterialTheme.colorScheme.surface) {
                    ShareTaskScreen(g, draft, prefs.morningHour) { msg ->
                        msg?.let { Toast.makeText(this, it, Toast.LENGTH_SHORT).show() }
                        finish()
                    }
                }
            }
        }
    }
}

/** The task a share becomes. Pure, so it is unit-tested. */
object ShareTask {
    private val URL = Regex("""https?://[^\s<>"']+""", RegexOption.IGNORE_CASE)

    /**
     * Title: the subject or title the app sent, else the first line that is not only a link,
     * else the link's host and path. Notes: the links, then any other text, each once.
     */
    fun draft(s: SharedContent, uid: String): TaskDraft {
        val text = s.text.orEmpty().trim()
        val links = URL.findAll(text).map { it.value.trimEnd('.', ',', ')', ';', '!', '?') }.distinct().toList()
        val title = SelfSubject.derive(s.subject, s.title, null, text, emptyList()).takeIf { it != SelfSubject.FALLBACK } ?: ""
        val rest = text.lines().map { it.trim() }.filter { it.isNotEmpty() && !SelfSubject.isLoneUrl(it) }
            .map { URL.replace(it, "").trim() }.filter { it.isNotEmpty() && it != title }
        val notes = (links + rest).joinToString("\n")
        return TaskDraft(uid = uid, summary = title.take(200), notes = notes)
    }
}

/** ACT task.create @android.share from a share: the editor, prefilled; Save adds the task (queued when offline). */
@Composable
fun ShareTaskScreen(g: AppGraph, draft: TaskDraft, morningHour: Int, done: (String?) -> Unit) {
    val scope = rememberCoroutineScope()
    val defaults by g.calendar.defaults.collectAsStateWithLifecycle()
    val cols by g.calendar.collections.collectAsStateWithLifecycle()
    var saving by remember { mutableStateOf(false) }
    val context = androidx.compose.ui.platform.LocalContext.current
    LaunchedEffect(Unit) { catching { g.calendar.loadCached() } }
    val s = remember { TaskEditorState(null, defaults.taskList, null, draft.uid, draft = draft) }
    LaunchedEffect(defaults.taskList) { if (s.list == null) s.list = defaults.taskList }
    androidx.compose.foundation.layout.Box(Modifier.safeDrawingPadding()) { TaskEditor(s, cols.filter { it.tasks && !it.readOnly }, emptyList(), morningHour, saving,
        onClose = { done(null) },
        onSave = {
            saving = true
            scope.launch {
                try {
                    val r = g.calendar.createTask(s.draft())
                    done(context.getString(if (r == WriteResult.QUEUED) R.string.tasks_share_queued else R.string.tasks_share_added))
                } catch (e: Exception) {
                    saving = false
                    Toast.makeText(context, e.userMessage(), Toast.LENGTH_LONG).show()
                }
            }
        },
        onDelete = null, onAddSubtask = null, onToggleSubtask = {}, onOpenSubtask = {}) }
}
