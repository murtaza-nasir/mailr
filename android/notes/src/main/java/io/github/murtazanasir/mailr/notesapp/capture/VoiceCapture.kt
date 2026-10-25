package io.github.murtazanasir.mailr.notesapp.capture

import io.github.murtazanasir.mailr.util.catching

import androidx.compose.ui.res.stringResource
import io.github.murtazanasir.mailr.notesapp.R
import io.github.murtazanasir.mailr.i18n.AppText
import android.content.Context
import android.content.Intent
import android.os.Build
import android.os.Bundle
import android.speech.RecognitionListener
import android.speech.RecognizerIntent
import android.speech.SpeechRecognizer
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import io.github.murtazanasir.mailr.look.Button
import io.github.murtazanasir.mailr.look.LinearProgressIndicator
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import io.github.murtazanasir.mailr.look.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.testTag
import androidx.compose.ui.semantics.LiveRegionMode
import androidx.compose.ui.semantics.liveRegion
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.unit.dp

/** What the voice-add sheet shows. */
sealed interface VoiceUi {
    data class Listening(val partial: String = "") : VoiceUi
    /** On-device recognition cannot run (none on the phone, no microphone permission, an error). */
    data class Stopped(val message: String, val retry: Boolean) : VoiceUi
}

/**
 * The phone's own recognizer for voice add (Android 12+): the audio stays on the phone. When the
 * phone has no on-device recognizer, voice add is not offered: no other speech service is tried.
 */
class OnDeviceRecognizer(private val context: Context) {
    private var r: SpeechRecognizer? = null

    fun start(onPartial: (String) -> Unit, onText: (String) -> Unit, onError: (Int) -> Unit) {
        destroy()
        if (Build.VERSION.SDK_INT < 31 || !available(context)) { onError(SpeechRecognizer.ERROR_CLIENT); return }
        val rec = catching { SpeechRecognizer.createOnDeviceSpeechRecognizer(context) }.getOrNull() ?: run { onError(SpeechRecognizer.ERROR_CLIENT); return }
        r = rec
        rec.setRecognitionListener(object : RecognitionListener {
            override fun onReadyForSpeech(params: Bundle?) {}
            override fun onBeginningOfSpeech() {}
            override fun onRmsChanged(rmsdB: Float) {}
            override fun onBufferReceived(buffer: ByteArray?) {}
            override fun onEndOfSpeech() {}
            override fun onError(error: Int) = onError(error)
            override fun onResults(results: Bundle?) = onText(best(results))
            override fun onPartialResults(partialResults: Bundle?) = onPartial(best(partialResults))
            override fun onEvent(eventType: Int, params: Bundle?) {}
        })
        rec.startListening(Intent(RecognizerIntent.ACTION_RECOGNIZE_SPEECH)
            .putExtra(RecognizerIntent.EXTRA_LANGUAGE_MODEL, RecognizerIntent.LANGUAGE_MODEL_FREE_FORM)
            .putExtra(RecognizerIntent.EXTRA_PARTIAL_RESULTS, true)
            .putExtra(RecognizerIntent.EXTRA_PREFER_OFFLINE, true)
            .putExtra(RecognizerIntent.EXTRA_CALLING_PACKAGE, context.packageName))
    }

    private fun best(b: Bundle?): String = b?.getStringArrayList(SpeechRecognizer.RESULTS_RECOGNITION)?.firstOrNull().orEmpty()

    fun stop() { r?.stopListening() }

    fun destroy() {
        r?.destroy()
        r = null
    }

    companion object {
        fun available(context: Context): Boolean = Build.VERSION.SDK_INT >= 31 &&
            catching { SpeechRecognizer.isOnDeviceRecognitionAvailable(context) }.getOrDefault(false)

        val UNAVAILABLE: String get() = AppText.get(R.string.notesapp_voice_unavailable)
        val NO_MIC: String get() = AppText.get(R.string.notesapp_voice_no_mic)

        fun message(code: Int): String = when (code) {
            SpeechRecognizer.ERROR_NO_MATCH, SpeechRecognizer.ERROR_SPEECH_TIMEOUT -> AppText.get(R.string.notesapp_voice_nothing_heard)
            SpeechRecognizer.ERROR_INSUFFICIENT_PERMISSIONS -> NO_MIC
            SpeechRecognizer.ERROR_AUDIO -> AppText.get(R.string.notesapp_voice_mic_busy)
            SpeechRecognizer.ERROR_RECOGNIZER_BUSY -> AppText.get(R.string.notesapp_voice_recognizer_busy)
            12, 13 -> AppText.get(R.string.notesapp_voice_no_model)
            SpeechRecognizer.ERROR_CLIENT -> UNAVAILABLE
            else -> AppText.get(R.string.notesapp_voice_error, code)
        }
    }
}

/** The sheet while voice add listens: what was heard so far, Stop and Cancel; or why it stopped. */
@Composable
fun VoiceSheet(state: VoiceUi, listName: String?, onStop: () -> Unit, onRetry: () -> Unit, onDone: () -> Unit) {
    SheetFrame(onDone) {
        Text(if (listName != null) stringResource(R.string.notesapp_add_to_list_named, listName) else stringResource(R.string.notesapp_add_to_a_list), style = MaterialTheme.typography.titleLarge,
            modifier = Modifier.padding(bottom = 8.dp))
        when (state) {
            is VoiceUi.Listening -> {
                Text(state.partial.ifBlank { if (listName != null) stringResource(R.string.notesapp_voice_listening_list) else stringResource(R.string.notesapp_voice_listening) },
                    style = MaterialTheme.typography.bodyLarge, modifier = Modifier.testTag("voice-heard").semantics { liveRegion = LiveRegionMode.Polite })
                LinearProgressIndicator(Modifier.fillMaxWidth().padding(top = 12.dp))
            }
            is VoiceUi.Stopped -> Text(state.message, style = MaterialTheme.typography.bodyLarge,
                modifier = Modifier.testTag("voice-message").semantics { liveRegion = LiveRegionMode.Polite })
        }
        Row(Modifier.fillMaxWidth().padding(top = 16.dp), horizontalArrangement = Arrangement.End) {
            TextButton(onClick = onDone) { Text(stringResource(if (state is VoiceUi.Listening) R.string.notesapp_cancel else R.string.notesapp_close)) }
            when {
                state is VoiceUi.Listening -> Button(onClick = onStop) { Text(stringResource(R.string.notesapp_done)) }
                state is VoiceUi.Stopped && state.retry -> Button(onClick = onRetry) { Text(stringResource(R.string.notesapp_try_again)) }
            }
        }
    }
}
