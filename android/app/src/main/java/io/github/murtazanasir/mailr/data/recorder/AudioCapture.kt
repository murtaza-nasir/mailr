package io.github.murtazanasir.mailr.data.recorder

import io.github.murtazanasir.mailr.util.catching

import android.content.Context
import android.media.MediaRecorder
import android.os.Build
import java.io.File

/** The microphone. [MediaCapture] on a phone; tests use a fake. */
interface AudioCapture {
    fun start(file: File)
    fun pause()
    fun resume()
    fun stop()
    /** The loudest sample since the last call, 0..1 (the level meter). */
    fun level(): Float
}

/**
 * MediaRecorder writing AAC in an ADTS stream: every frame stands alone, so a file cut off by a
 * kill or a crash still plays up to its last frame (an .m4a needs its index written at the end).
 * Mono, 32 kHz, 64 kbit/s: about 29 MB an hour, enough for speech.
 */
class MediaCapture(private val context: Context) : AudioCapture {
    private var rec: MediaRecorder? = null

    override fun start(file: File) {
        val r = if (Build.VERSION.SDK_INT >= 31) MediaRecorder(context) else @Suppress("DEPRECATION") MediaRecorder()
        r.setAudioSource(MediaRecorder.AudioSource.MIC)
        r.setOutputFormat(MediaRecorder.OutputFormat.AAC_ADTS)
        r.setAudioEncoder(MediaRecorder.AudioEncoder.AAC)
        r.setAudioChannels(1)
        r.setAudioSamplingRate(32_000)
        r.setAudioEncodingBitRate(64_000)
        r.setOutputFile(file.absolutePath)
        r.prepare()
        r.start()
        rec = r
    }

    override fun pause() {
        rec?.pause()
    }

    override fun resume() {
        rec?.resume()
    }

    override fun stop() {
        val r = rec ?: return
        rec = null
        catching { r.stop() }
        r.release()
    }

    override fun level(): Float = (rec?.let { catching { it.maxAmplitude }.getOrDefault(0) } ?: 0) / 32767f
}
