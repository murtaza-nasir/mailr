package io.github.murtazanasir.mailr.ui.ask

import android.content.Context
import io.github.murtazanasir.mailr.AppGraph
import io.github.murtazanasir.mailr.data.voice.Dictation
import io.github.murtazanasir.mailr.data.voice.DictationOrder
import io.github.murtazanasir.mailr.data.voice.MediaClipRecorder
import io.github.murtazanasir.mailr.data.voice.OnDeviceSpeechEngine
import io.github.murtazanasir.mailr.data.voice.ServerSpeechEngine
import io.github.murtazanasir.mailr.data.voice.VoicePolicy
import io.github.murtazanasir.mailr.data.voice.VoiceSource
import kotlinx.coroutines.CoroutineScope

/**
 * The recognizers dictation may use on this phone, for the ask capture and the
 * Assistant tab's message box. The phone's on-device recognizer, or the user's own speech server
 * through mailr (POST /assistant/transcribe) when GET /me says it can transcribe. The server sends a
 * clip to a hosted speech service only when its [clipContext] holds nothing local-only. [server] can
 * change once the app learns what the server offers.
 *
 * ACT agent.dictate @android.assistant
 */
class VoiceSetup(
    private val context: Context,
    private val g: AppGraph,
    private val scope: CoroutineScope,
    private val handsFree: Boolean,
    var server: Boolean,
) {
    /** What the clip is spoken into, asked when it is sent (DictationContext). */
    var clipContext: () -> String? = { "none" }

    val onDevice: Boolean = VoicePolicy.onDeviceAvailable(context)

    fun engines(s: VoiceSource): Dictation? = when (s) {
        VoiceSource.ON_DEVICE -> if (onDevice) Dictation(OnDeviceSpeechEngine(context, handsFree)) else null
        VoiceSource.SERVER -> if (server && g.sessions.session.value != null) Dictation(ServerSpeechEngine(
            java.io.File(context.cacheDir, "dictation"), { MediaClipRecorder(context) },
            { f, ms, lang -> g.api.transcribe(f, ms, lang, clipContext()) }, scope, handsFree)) else null
        VoiceSource.NONE -> null
    }

    fun source(order: DictationOrder): VoiceSource = VoicePolicy.source(onDevice, server, order)

    fun fallback(current: VoiceSource): VoiceSource? = VoicePolicy.fallback(current, onDevice, server)
}
