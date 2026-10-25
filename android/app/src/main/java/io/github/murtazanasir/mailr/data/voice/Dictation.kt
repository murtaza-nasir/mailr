package io.github.murtazanasir.mailr.data.voice

import io.github.murtazanasir.mailr.util.catching

import androidx.annotation.StringRes
import io.github.murtazanasir.mailr.i18n.AppText
import io.github.murtazanasir.mailr.R
import android.content.Context
import android.content.Intent
import android.os.Build
import android.os.Bundle
import android.speech.RecognitionListener
import android.speech.RecognizerIntent
import android.speech.SpeechRecognizer
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow

/**
 * Where dictated speech is turned into text. The phone's recognizer works offline; the server may use a
 * hosted speech service only for clips whose context holds nothing local-only (agent/transcribe.py).
 *
 * - [ON_DEVICE]: the phone's on-device recognizer (Android 12+, when the phone has one). Audio
 *   stays on the phone.
 * - [SERVER]: a short clip goes through mailr to the Speech to text endpoint (MAILR_ASR_URL, a
 *   local WhisperX, or a hosted service an admin added); GET /me reports it as
 *   `features.transcribe`. The request names its context (DictationContext); the server keeps a
 *   clip of a local-only context on a local endpoint. The clip is not kept.
 * - [NONE]: no dictation; the keyboard stays.
 *
 * Local models only (the household rule or the person's switch, GET /me/local-models) needs no
 * extra branch here: the server applies it to the Speech to text task, the phone's default (possibly cloud) recognizer
 * from SpeechRecognizer.createSpeechRecognizer is never created (VoiceLocalOnlyTest checks the
 * sources), and the on-device request asks for offline recognition (EXTRA_PREFER_OFFLINE).
 * When neither source exists, the capture says so and keeps the keyboard.
 */
enum class VoiceSource { ON_DEVICE, SERVER, NONE }

/** Settings → Dictation: which recognizer is tried first. */
enum class DictationOrder(@StringRes val labelRes: Int) {
    PHONE_FIRST(R.string.voice_order_phone_first), SERVER(R.string.voice_order_server);

    val label: String get() = AppText.get(labelRes)
}

object VoicePolicy {
    /** The source to use. The other one, when it exists, is the fallback. */
    fun source(onDevice: Boolean, server: Boolean, order: DictationOrder): VoiceSource = when (order) {
        DictationOrder.PHONE_FIRST -> if (onDevice) VoiceSource.ON_DEVICE else if (server) VoiceSource.SERVER else VoiceSource.NONE
        DictationOrder.SERVER -> if (server) VoiceSource.SERVER else if (onDevice) VoiceSource.ON_DEVICE else VoiceSource.NONE
    }

    /** Where to go when [current] cannot be used (no model for the language, recognizer refused). */
    fun fallback(current: VoiceSource, onDevice: Boolean, server: Boolean): VoiceSource? = when (current) {
        VoiceSource.ON_DEVICE -> VoiceSource.SERVER.takeIf { server }
        VoiceSource.SERVER -> VoiceSource.ON_DEVICE.takeIf { onDevice }
        VoiceSource.NONE -> null
    }

    fun onDeviceAvailable(context: Context): Boolean = Build.VERSION.SDK_INT >= 31 &&
        catching { SpeechRecognizer.isOnDeviceRecognitionAvailable(context) }.getOrDefault(false)

    /** The settings line under Dictation: what this phone will do with the user's voice. */
    fun describe(onDevice: Boolean, server: Boolean, order: DictationOrder): String {
        val phone = AppText.get(if (onDevice) R.string.voice_phone_has_recognizer else R.string.voice_phone_no_recognizer)
        val srv = AppText.get(if (server) R.string.voice_server_can_transcribe else R.string.voice_server_none)
        val now = when (source(onDevice, server, order)) {
            VoiceSource.ON_DEVICE -> AppText.get(R.string.voice_now_on_device)
            VoiceSource.SERVER -> AppText.get(R.string.voice_now_server)
            VoiceSource.NONE -> AppText.get(R.string.voice_now_none)
        }
        return "$phone $srv $now ${AppText.get(R.string.voice_hosted_rule)}"
    }
}

/** What the capture shows while dictating. */
sealed interface VoiceState {
    data object Idle : VoiceState
    /** Listening; [partial] is the text so far, [level] the input level from 0 to 1. */
    data class Listening(val partial: String = "", val level: Float = 0f) : VoiceState
    /** The clip is with the speech server (SERVER only). */
    data object Transcribing : VoiceState
    /** The recognizer finished with [text] (may be empty when nothing was heard). */
    data class Heard(val text: String) : VoiceState
    /**
     * [download]: the on-device model for the language is missing and can be fetched (Android 13+).
     * [unusable]: this recognizer cannot serve the request at all; the capture tries the other one.
     */
    data class Failed(val message: String, val download: Boolean = false, val unusable: Boolean = false) : VoiceState
}

/** The recognizer behind [Dictation] (the platform's in the app, a fake in tests). */
interface SpeechEngine {
    fun start(language: String?, events: Events)
    fun stop()
    fun cancel()
    fun destroy()
    /** Asks the on-device recognizer to fetch the language model (Android 13+); false if it cannot. */
    fun download(language: String?): Boolean = false

    interface Events {
        fun ready()
        fun level(rms: Float)
        fun partial(text: String)
        fun final(text: String)
        fun error(code: Int)
        /** The recording stopped and the clip is being transcribed (SERVER only). */
        fun transcribing() {}
        /** A failure with its own message (SERVER: the server's answer). */
        fun failed(message: String) {}
    }
}

/**
 * One dictation at a time. [start] listens; the text arrives in [state] as it is heard. The
 * caller decides what to do with the final text (the capture shows it editable, or sends it in
 * hands-free mode).
 */
class Dictation(private val engine: SpeechEngine) {
    private val _state = MutableStateFlow<VoiceState>(VoiceState.Idle)
    val state: StateFlow<VoiceState> = _state.asStateFlow()

    fun start(language: String? = null) {
        _state.value = VoiceState.Listening()
        engine.start(language, object : SpeechEngine.Events {
            override fun ready() {
                if (_state.value !is VoiceState.Listening) _state.value = VoiceState.Listening()
            }

            override fun level(rms: Float) {
                val s = _state.value as? VoiceState.Listening ?: return
                // RMS dB arrives roughly from -2 to 10.
                _state.value = s.copy(level = ((rms + 2f) / 12f).coerceIn(0f, 1f))
            }

            override fun partial(text: String) {
                val s = _state.value as? VoiceState.Listening ?: return
                if (text.isNotBlank()) _state.value = s.copy(partial = text)
            }

            override fun final(text: String) {
                val before = (_state.value as? VoiceState.Listening)?.partial.orEmpty()
                val heard = text.ifBlank { before }.trim()
                // Nothing came back (the speech server drops silence and Whisper's stock phrases): say so.
                _state.value = if (heard.isEmpty()) VoiceState.Failed(NO_SPEECH) else VoiceState.Heard(heard)
            }

            override fun error(code: Int) {
                val partial = (_state.value as? VoiceState.Listening)?.partial.orEmpty()
                _state.value = when {
                    // Silence or no match after some words: keep what was heard.
                    (code == ERROR_NO_MATCH || code == ERROR_SPEECH_TIMEOUT) && partial.isNotBlank() -> VoiceState.Heard(partial)
                    else -> VoiceState.Failed(message(code), download = code == ERROR_LANGUAGE_UNAVAILABLE,
                        unusable = code in UNUSABLE)
                }
            }

            override fun transcribing() {
                _state.value = VoiceState.Transcribing
            }

            override fun failed(message: String) {
                _state.value = VoiceState.Failed(message)
            }
        })
    }

    fun stop() = engine.stop()

    fun cancel() {
        engine.cancel()
        _state.value = VoiceState.Idle
    }

    fun reset() {
        _state.value = VoiceState.Idle
    }

    fun download(language: String? = null): Boolean = engine.download(language)

    fun destroy() = engine.destroy()

    companion object {
        // SpeechRecognizer error codes (stable platform constants; 12 and 13 are API 31+).
        const val ERROR_NETWORK = 2
        const val ERROR_AUDIO = 3
        const val ERROR_CLIENT = 5
        const val ERROR_SPEECH_TIMEOUT = 6
        const val ERROR_NO_MATCH = 7
        const val ERROR_BUSY = 8
        const val ERROR_PERMISSIONS = 9
        const val ERROR_LANGUAGE_NOT_SUPPORTED = 12
        const val ERROR_LANGUAGE_UNAVAILABLE = 13
        /** The recognizer finished with no words (the server answers {"text": "", "dropped": "no speech"}). */
        val NO_SPEECH: String get() = AppText.get(R.string.voice_no_speech)
        /** The recognizer cannot be used for this request: the capture falls back to the other source. */
        val UNUSABLE = setOf(ERROR_CLIENT, ERROR_NETWORK, 1, 4, ERROR_LANGUAGE_NOT_SUPPORTED, ERROR_LANGUAGE_UNAVAILABLE, 10, 11)

        fun message(code: Int): String = when (code) {
            ERROR_NO_MATCH, ERROR_SPEECH_TIMEOUT -> AppText.get(R.string.voice_err_no_match)
            ERROR_PERMISSIONS -> AppText.get(R.string.voice_err_permissions)
            ERROR_NETWORK, 1, 4 -> AppText.get(R.string.voice_err_network)
            ERROR_BUSY -> AppText.get(R.string.voice_err_busy)
            ERROR_AUDIO -> AppText.get(R.string.voice_err_audio)
            ERROR_LANGUAGE_NOT_SUPPORTED -> AppText.get(R.string.voice_err_language_not_supported)
            ERROR_LANGUAGE_UNAVAILABLE -> AppText.get(R.string.voice_err_language_unavailable)
            else -> AppText.get(R.string.voice_err_other, code)
        }
    }
}

/**
 * The phone's on-device recognizer (Android 12+). The phone's default speech service is never
 * used: it may send audio to its maker. Must be used on the main thread.
 */
class OnDeviceSpeechEngine(private val context: Context, private val handsFree: Boolean) : SpeechEngine {
    private var recognizer: SpeechRecognizer? = null

    override fun start(language: String?, events: SpeechEngine.Events) {
        recognizer?.destroy()
        if (Build.VERSION.SDK_INT < 31) { events.error(Dictation.ERROR_CLIENT); return }
        val r = catching { SpeechRecognizer.createOnDeviceSpeechRecognizer(context) }.getOrNull()
            ?: run { events.error(Dictation.ERROR_CLIENT); return }
        recognizer = r
        r.setRecognitionListener(object : RecognitionListener {
            override fun onReadyForSpeech(params: Bundle?) = events.ready()
            override fun onBeginningOfSpeech() {}
            override fun onRmsChanged(rmsdB: Float) = events.level(rmsdB)
            override fun onBufferReceived(buffer: ByteArray?) {}
            override fun onEndOfSpeech() {}
            override fun onError(error: Int) = events.error(error)
            override fun onResults(results: Bundle?) = events.final(best(results))
            override fun onPartialResults(partialResults: Bundle?) = events.partial(best(partialResults))
            override fun onEvent(eventType: Int, params: Bundle?) {}
        })
        r.startListening(intent(language))
    }

    private fun best(b: Bundle?): String = b?.getStringArrayList(SpeechRecognizer.RESULTS_RECOGNITION)?.firstOrNull().orEmpty()

    private fun intent(language: String?) = Intent(RecognizerIntent.ACTION_RECOGNIZE_SPEECH)
        .putExtra(RecognizerIntent.EXTRA_LANGUAGE_MODEL, RecognizerIntent.LANGUAGE_MODEL_FREE_FORM)
        .putExtra(RecognizerIntent.EXTRA_PARTIAL_RESULTS, true)
        .putExtra(RecognizerIntent.EXTRA_PREFER_OFFLINE, true)
        .putExtra(RecognizerIntent.EXTRA_CALLING_PACKAGE, context.packageName)
        .apply {
            language?.let { putExtra(RecognizerIntent.EXTRA_LANGUAGE, it) }
            // Hands-free: a pause of about two seconds ends the instruction.
            if (handsFree) putExtra(RecognizerIntent.EXTRA_SPEECH_INPUT_COMPLETE_SILENCE_LENGTH_MILLIS, 2_000L)
        }

    override fun stop() { recognizer?.stopListening() }

    override fun cancel() { recognizer?.cancel() }

    override fun destroy() {
        recognizer?.destroy()
        recognizer = null
    }

    override fun download(language: String?): Boolean {
        if (Build.VERSION.SDK_INT < 33) return false
        return catching {
            val r = recognizer ?: SpeechRecognizer.createOnDeviceSpeechRecognizer(context).also { recognizer = it }
            r.triggerModelDownload(intent(language))
            true
        }.getOrDefault(false)
    }
}
