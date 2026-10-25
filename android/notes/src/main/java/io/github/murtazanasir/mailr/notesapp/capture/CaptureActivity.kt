package io.github.murtazanasir.mailr.notesapp.capture

import io.github.murtazanasir.mailr.util.catching

import io.github.murtazanasir.mailr.notesapp.ui.followHideContent

import androidx.compose.ui.res.stringResource
import io.github.murtazanasir.mailr.notesapp.R
import io.github.murtazanasir.mailr.i18n.AppText
import android.app.PendingIntent
import android.content.Context
import android.content.Intent
import android.os.Build
import android.os.Bundle
import android.service.quicksettings.TileService
import android.speech.RecognizerIntent
import android.widget.Toast
import androidx.activity.ComponentActivity
import androidx.activity.compose.setContent
import androidx.activity.enableEdgeToEdge
import androidx.activity.result.contract.ActivityResultContracts
import android.Manifest
import android.content.pm.PackageManager
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.setValue
import androidx.core.content.ContextCompat
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import androidx.lifecycle.lifecycleScope
import io.github.murtazanasir.mailr.notes.data.notesMessage
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.flow.first
import kotlinx.coroutines.launch
import io.github.murtazanasir.mailr.notesapp.MainActivity
import io.github.murtazanasir.mailr.notesapp.NotesApplication
import io.github.murtazanasir.mailr.notesapp.ui.NotesTheme

/**
 * Quick capture over whatever is on screen: from the Quick Settings tile, the capture widget and the
 * checklist widget's "+". Extras: [EXTRA_MODE] ("note", "list", "add", or "voice": voice add),
 * [EXTRA_LIST] (a list's id), [EXTRA_DICTATE] (start with the microphone).
 */
class CaptureActivity : ComponentActivity() {
    // The app's language before Android 13; a no-op from 13 on.
    override fun attachBaseContext(newBase: android.content.Context) {
        super.attachBaseContext(io.github.murtazanasir.mailr.i18n.AppLocales.wrap(newBase))
    }

    private var onSpoken: ((String) -> Unit)? = null
    private val speech = registerForActivityResult(ActivityResultContracts.StartActivityForResult()) { r ->
        val said = r.data?.getStringArrayListExtra(RecognizerIntent.EXTRA_RESULTS)?.firstOrNull()
        if (!said.isNullOrBlank()) onSpoken?.invoke(said)
    }

    private fun dictate(onText: (String) -> Unit) {
        onSpoken = onText
        val i = Intent(RecognizerIntent.ACTION_RECOGNIZE_SPEECH)
            .putExtra(RecognizerIntent.EXTRA_LANGUAGE_MODEL, RecognizerIntent.LANGUAGE_MODEL_FREE_FORM)
            .putExtra(RecognizerIntent.EXTRA_PROMPT, "Say your note")
        catching { speech.launch(i) }.onFailure {
            Toast.makeText(this, AppText.get(R.string.notesapp_no_speech_recognizer), Toast.LENGTH_SHORT).show()
        }
    }

    // ================================================================ voice add

    private var voice by mutableStateOf<VoiceUi?>(null)
    /** Words that named no list, kept as a note in the capture sheet. */
    private var voiceNote by mutableStateOf<String?>(null)
    private var recognizer: OnDeviceRecognizer? = null
    private val micPermission = registerForActivityResult(ActivityResultContracts.RequestPermission()) { granted ->
        if (granted) listen() else voice = VoiceUi.Stopped(OnDeviceRecognizer.NO_MIC, retry = false)
    }

    /** On-device recognition only: no recognizer on the phone, or no microphone, says so and stops. */
    private fun startVoice() {
        when {
            !OnDeviceRecognizer.available(this) -> voice = VoiceUi.Stopped(OnDeviceRecognizer.UNAVAILABLE, retry = false)
            ContextCompat.checkSelfPermission(this, Manifest.permission.RECORD_AUDIO) != PackageManager.PERMISSION_GRANTED ->
                micPermission.launch(Manifest.permission.RECORD_AUDIO)
            else -> listen()
        }
    }

    private fun listen() {
        voice = VoiceUi.Listening()
        val r = recognizer ?: OnDeviceRecognizer(this).also { recognizer = it }
        r.start(onPartial = { p -> if (voice is VoiceUi.Listening && p.isNotBlank()) voice = VoiceUi.Listening(p) },
            onText = { t -> heard(t) },
            onError = { code ->
                val partial = (voice as? VoiceUi.Listening)?.partial.orEmpty()
                if (partial.isNotBlank()) heard(partial)
                else voice = VoiceUi.Stopped(OnDeviceRecognizer.message(code), retry = code != android.speech.SpeechRecognizer.ERROR_CLIENT)
            })
    }

    private fun heard(text: String) {
        if (text.isBlank()) { voice = VoiceUi.Stopped(AppText.get(R.string.notesapp_voice_nothing_heard), retry = true); return }
        val g = (application as NotesApplication).graph
        val fallback = intent.getStringExtra(EXTRA_LIST)
        lifecycleScope.launch {
            try {
                val lists = CaptureLogic.lists(g.notes.repo.notes.first(), g.notes.repo.sectionsByNote.first())
                val out = VoicePhrase.apply(g.notes.repo, text, lists, fallback)
                when {
                    out.noteText != null -> { voice = null; voiceNote = out.noteText }
                    else -> {
                        out.listId?.let { g.prefs.opened("note", it) }
                        Toast.makeText(this@CaptureActivity, out.message, Toast.LENGTH_SHORT).show()
                        if (out.listId != null) finish() else voice = VoiceUi.Stopped(out.message.orEmpty(), retry = true)
                    }
                }
            } catch (e: CancellationException) { throw e } catch (e: Exception) {
                voice = VoiceUi.Stopped(e.notesMessage(), retry = true)
            }
        }
    }

    override fun onDestroy() {
        recognizer?.destroy()
        super.onDestroy()
    }

    override fun onCreate(savedInstanceState: Bundle?) {
        enableEdgeToEdge()
        super.onCreate(savedInstanceState)
        val g = (application as NotesApplication).graph
        followHideContent(g.prefs)
        val voiceMode = intent.getStringExtra(EXTRA_MODE) == "voice"
        if (voiceMode && savedInstanceState == null && g.sessions.session.value != null) startVoice()
        val mode = when (intent.getStringExtra(EXTRA_MODE)) {
            "list" -> CaptureMode.LIST
            "add" -> CaptureMode.ADD
            else -> CaptureMode.NOTE
        }
        val list = intent.getStringExtra(EXTRA_LIST)
        val startDictation = savedInstanceState == null && intent.getBooleanExtra(EXTRA_DICTATE, false)
        setContent {
            val look by g.prefs.look.collectAsStateWithLifecycle()
            val session by g.sessions.session.collectAsStateWithLifecycle()
            NotesTheme(look) {
                if (session == null) {
                    NoticeSheet(stringResource(R.string.notesapp_sign_in_to_notes), stringResource(R.string.notesapp_sign_in_first), stringResource(R.string.notesapp_open_notes),
                        onAction = { startActivity(Intent(this, MainActivity::class.java)); finish() }, onDone = ::finish)
                } else if (voiceMode && voiceNote == null) {
                    val listName = list?.let { id -> g.notes.repo.notes.collectAsStateWithLifecycle(emptyList()).value.firstOrNull { it.id == id }?.title }
                    VoiceSheet(voice ?: VoiceUi.Listening(), listName, onStop = { recognizer?.stop() }, onRetry = ::startVoice, onDone = ::finish)
                } else CaptureSheet(g.notes.repo, g.prefs, mode, list, ::dictate, dictateNow = startDictation,
                    initialText = voiceNote.orEmpty()) { msg ->
                    if (msg != null) Toast.makeText(this, msg, Toast.LENGTH_SHORT).show()
                    finish()
                }
            }
        }
    }

    companion object {
        const val EXTRA_MODE = "mode"
        const val EXTRA_LIST = "list"
        const val EXTRA_DICTATE = "dictate"

        fun intent(context: Context, mode: String = "note", list: String? = null, dictate: Boolean = false): Intent =
            Intent(context, CaptureActivity::class.java).putExtra(EXTRA_MODE, mode).putExtra(EXTRA_DICTATE, dictate)
                .apply { list?.let { putExtra(EXTRA_LIST, it) } }
                .addFlags(Intent.FLAG_ACTIVITY_NEW_TASK or Intent.FLAG_ACTIVITY_CLEAR_TASK)
    }
}

/**
 * The Quick Settings tile "New note": opens quick capture over the
 * shade. ACT note.create @android.noteslauncher
 */
class NewNoteTile : TileService() {
    override fun onClick() {
        super.onClick()
        val i = CaptureActivity.intent(this, "note")
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.UPSIDE_DOWN_CAKE) {
            startActivityAndCollapse(PendingIntent.getActivity(this, 0, i, PendingIntent.FLAG_IMMUTABLE or PendingIntent.FLAG_UPDATE_CURRENT))
        } else {
            @Suppress("DEPRECATION", "StartActivityAndCollapseDeprecated")
            startActivityAndCollapse(i)
        }
    }
}
