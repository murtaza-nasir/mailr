package io.github.murtazanasir.mailr.data.voice

import io.github.murtazanasir.mailr.util.catching

import io.github.murtazanasir.mailr.data.api.shownDetail
import io.github.murtazanasir.mailr.i18n.AppText
import io.github.murtazanasir.mailr.R
import android.content.Context
import android.media.MediaRecorder
import android.os.Build
import io.github.murtazanasir.mailr.data.api.ApiException
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Job
import kotlinx.coroutines.delay
import kotlinx.coroutines.launch
import java.io.File
import kotlin.math.sqrt

/** The microphone for a dictated clip (MediaRecorder in the app, a fake in tests). */
interface ClipRecorder {
    fun start(file: File)
    /** The loudest sample since the last call, 0 to 32767. */
    fun amplitude(): Int
    /** Stops; false when nothing usable was recorded. */
    fun stop(): Boolean
    fun release()
}

/** AAC in MP4 (m4a), 16 kHz mono: small, and the speech server reads it as it is. */
class MediaClipRecorder(private val context: Context) : ClipRecorder {
    private var r: MediaRecorder? = null

    override fun start(file: File) {
        val m = if (Build.VERSION.SDK_INT >= 31) MediaRecorder(context) else @Suppress("DEPRECATION") MediaRecorder()
        m.setAudioSource(MediaRecorder.AudioSource.VOICE_RECOGNITION)
        m.setOutputFormat(MediaRecorder.OutputFormat.MPEG_4)
        m.setAudioEncoder(MediaRecorder.AudioEncoder.AAC)
        m.setAudioSamplingRate(16_000)
        m.setAudioChannels(1)
        m.setAudioEncodingBitRate(32_000)
        m.setOutputFile(file.absolutePath)
        m.prepare()
        m.start()
        r = m
    }

    override fun amplitude(): Int = catching { r?.maxAmplitude ?: 0 }.getOrDefault(0)

    override fun stop(): Boolean = catching { r?.stop(); true }.getOrDefault(false)

    override fun release() {
        catching { r?.release() }
        r = null
    }
}

/**
 * Dictation through mailr to the user's own speech server (VoiceSource.SERVER).
 *
 * The clip is recorded on the phone until Stop, or, in hands-free mode, until about two seconds of
 * quiet after speech. It then goes to POST /assistant/transcribe and the text comes back as the
 * final result. The clip file is deleted when the request ends, whatever the outcome. Clips stop
 * by themselves at [maxMs].
 */
class ServerSpeechEngine(
    private val dir: File,
    private val recorder: () -> ClipRecorder,
    private val upload: suspend (File, Long, String?) -> String,
    private val scope: CoroutineScope,
    private val handsFree: Boolean,
    private val now: () -> Long = System::currentTimeMillis,  // clock-ok: clip durations and file names
    private val maxMs: Long = 120_000,
) : SpeechEngine {
    private var rec: ClipRecorder? = null
    private var file: File? = null
    private var startedAt = 0L
    private var language: String? = null
    private var poll: Job? = null
    private var work: Job? = null
    private var events: SpeechEngine.Events? = null

    override fun start(language: String?, events: SpeechEngine.Events) {
        cancel()
        this.events = events
        this.language = language
        dir.mkdirs()
        val f = File(dir, "clip-${now()}.m4a")
        val r = recorder()
        try {
            r.start(f)
        } catch (e: Exception) {
            r.release()
            f.delete()
            events.error(Dictation.ERROR_AUDIO)
            return
        }
        rec = r
        file = f
        startedAt = now()
        events.ready()
        poll = scope.launch {
            var spoke = false
            var quietSince = 0L
            while (true) {
                delay(100)
                val amp = r.amplitude()
                // The level bar: 0 to 1 on a square-root scale, sent as the recognizer's dB range.
                events.level(-2f + 12f * sqrt(amp / 32767f))
                val t = now()
                if (t - startedAt >= maxMs) { finish(); break }
                if (!handsFree) continue
                if (amp > SPEECH) { spoke = true; quietSince = 0L }
                else if (spoke) {
                    if (quietSince == 0L) quietSince = t
                    if (t - quietSince >= QUIET_MS) { finish(); break }
                } else if (t - startedAt >= NOTHING_MS) {
                    discard()
                    events.error(Dictation.ERROR_SPEECH_TIMEOUT)
                    break
                }
            }
        }
    }

    /** Stop: the clip goes to the server and its text comes back. */
    override fun stop() = finish()

    private fun finish() {
        val r = rec ?: return
        val f = file ?: return
        val ev = events ?: return
        poll?.cancel()
        rec = null
        file = null
        val ok = r.stop()
        r.release()
        val dur = now() - startedAt
        if (!ok || dur < 400) {
            f.delete()
            ev.error(Dictation.ERROR_NO_MATCH)
            return
        }
        ev.transcribing()
        work = scope.launch {
            try {
                ev.final(upload(f, dur, language))
            } catch (e: ApiException) {
                ev.failed(e.shownDetail().ifBlank { AppText.get(R.string.voice_server_failed) })
            } catch (e: kotlinx.coroutines.CancellationException) {
                throw e
            } catch (e: Exception) {
                ev.failed(AppText.get(R.string.voice_server_unreachable))
            } finally {
                f.delete()
            }
        }
    }

    private fun discard() {
        poll?.cancel()
        rec?.let { catching { it.stop() }; it.release() }
        rec = null
        file?.delete()
        file = null
    }

    override fun cancel() {
        discard()
        work?.cancel()
        work = null
    }

    override fun destroy() = cancel()

    companion object {
        const val SPEECH = 2_000
        const val QUIET_MS = 2_000L
        const val NOTHING_MS = 8_000L
    }
}
