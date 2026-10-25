package io.github.murtazanasir.mailr.data.recorder

import io.github.murtazanasir.mailr.i18n.AppText
import io.github.murtazanasir.mailr.R
import io.github.murtazanasir.mailr.data.api.ApiException
import io.github.murtazanasir.mailr.data.api.SpeakrApi
import io.github.murtazanasir.mailr.data.api.userMessage
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Job
import kotlinx.coroutines.delay
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.flow.update
import kotlinx.coroutines.isActive
import kotlinx.coroutines.launch
import kotlinx.coroutines.sync.Mutex
import kotlinx.coroutines.sync.withLock
import java.util.UUID

/** What an upload attempt came to. */
enum class UploadOutcome { DONE, RETRY, FINAL }

/** What the controller asks of Android: the foreground service and the upload queue. */
interface RecorderHooks {
    /** Start (or update) the foreground service with its notification. */
    fun recording(session: RecordingSession)
    /** The recording ended: the service stops. */
    fun idle()
    /** Upload [id] when there is a network, retrying with back-off (RecordingUploadWork). */
    fun enqueueUpload(id: String)
}

/**
 * The phone's recorder: one recording at a time, kept on disk as it goes ([RecordingStore]).
 * The microphone works through [capture]; the foreground service keeps the app alive while it
 * records ([RecorderHooks.recording]); uploads go through mailr with [api]. [recover] runs once
 * per process: a record left RECORDING or PAUSED means the app was killed, so it stops at its
 * last heartbeat and is uploaded.
 */
class RecorderController(
    private val store: RecordingStore,
    private val capture: () -> AudioCapture,
    private val api: () -> SpeakrApi,
    private val scope: CoroutineScope,
    private val hooks: RecorderHooks,
    private val now: () -> Long = System::currentTimeMillis,  // clock-ok: recording durations
    /** Heartbeat and level-meter period (ms). */
    private val tickMs: Long = 100,
) {
    private val _current = MutableStateFlow<RecordingSession?>(null)
    /** The recording on the New recording screen: live, or stopped and uploading. */
    val current: StateFlow<RecordingSession?> = _current.asStateFlow()

    private val _all = MutableStateFlow<List<RecordingSession>>(emptyList())
    /** Every record on the phone (recordings waiting to upload, failed ones, recent uploads). */
    val all: StateFlow<List<RecordingSession>> = _all.asStateFlow()

    private val _level = MutableStateFlow(0f)
    /** The microphone level, 0..1, while recording. */
    val level: StateFlow<Float> = _level.asStateFlow()

    private val _progress = MutableStateFlow<Map<String, Float>>(emptyMap())
    /** Upload progress by recording id, 0..1. */
    val progress: StateFlow<Map<String, Float>> = _progress.asStateFlow()

    private var mic: AudioCapture? = null
    private var ticker: Job? = null
    private val lock = Mutex()
    private val uploads = Mutex()
    @Volatile private var recovered = false

    private fun put(s: RecordingSession): RecordingSession {
        store.save(s)
        if (_current.value?.id == s.id || s.live) _current.value = s
        _all.update { list -> listOf(s) + list.filter { it.id != s.id } }
        return s
    }

    /** Reload the records; the first call in a process stops and queues what a kill interrupted. */
    fun recover() {
        if (!recovered) {
            recovered = true
            store.all().forEach { s ->
                if (s.live || s.state == RecState.UPLOADING) {
                    val r = store.save(RecorderMachine.recover(s))
                    if (r.state == RecState.STOPPED) hooks.enqueueUpload(r.id)
                    if (_current.value == null) _current.value = r
                } else if (s.state == RecState.STOPPED) hooks.enqueueUpload(s.id)
            }
            store.prune(now())
        }
        _all.value = store.all()
    }

    val isRecording: Boolean get() = _current.value?.live == true

    /** Start recording to a new file. Throws when the microphone cannot start. */
    suspend fun start(meta: RecordingMeta): RecordingSession = lock.withLock {
        check(_current.value?.live != true) { "A recording is already running" }
        val id = UUID.randomUUID().toString()
        val s = RecorderMachine.start(id, "$id.aac", meta, now())
        val c = capture()
        c.start(store.audio(s))
        mic = c
        put(s)
        hooks.recording(s)
        tick()
        s
    }

    suspend fun pause() = lock.withLock {
        val s = _current.value ?: return@withLock
        if (s.state != RecState.RECORDING) return@withLock
        mic?.pause()
        hooks.recording(put(RecorderMachine.pause(s, now())))
        _level.value = 0f
    }

    suspend fun resume() = lock.withLock {
        val s = _current.value ?: return@withLock
        if (s.state != RecState.PAUSED) return@withLock
        mic?.resume()
        hooks.recording(put(RecorderMachine.resume(s, now())))
    }

    /** Stop and queue the upload. */
    suspend fun stop() = lock.withLock {
        val s = _current.value ?: return@withLock
        if (!s.live) return@withLock
        ticker?.cancel()
        mic?.stop()
        mic = null
        _level.value = 0f
        put(RecorderMachine.stop(s, now()))
        hooks.idle()
        hooks.enqueueUpload(s.id)
    }

    /** The notes as the user types (kept on disk, so a kill keeps them too). */
    fun setNotes(markdown: String) {
        val s = _current.value ?: return
        if (s.meta.notes != markdown) put(RecorderMachine.notes(s, markdown))
    }

    /** Title, people, date, folder, tags… changed while recording or before the upload. */
    fun setMeta(meta: RecordingMeta) {
        val s = _current.value ?: return
        if (s.state == RecState.UPLOADING || s.state == RecState.UPLOADED) return
        put(RecorderMachine.meta(s, meta))
    }

    /** Leave the finished recording (the screen starts fresh); its upload goes on. */
    fun dismiss() {
        if (_current.value?.live != true) _current.value = null
    }

    /** Show a record on the screen (from the list of waiting uploads). */
    fun select(id: String) {
        if (_current.value?.live == true) return
        _current.value = store.load(id)
    }

    /** "Try again" on a failed upload. */
    fun retry(id: String) {
        val s = store.load(id) ?: return
        put(RecorderMachine.retry(s))
        hooks.enqueueUpload(id)
    }

    /** Throw away a recording that is not live (its audio too). */
    fun discard(id: String) {
        val s = store.load(id) ?: return
        if (s.live) return
        store.delete(s)
        if (_current.value?.id == id) _current.value = null
        _all.update { list -> list.filter { it.id != id } }
    }

    private fun tick() {
        ticker?.cancel()
        ticker = scope.launch {
            var n = 0
            while (isActive) {
                delay(tickMs)
                val s = _current.value ?: break
                if (!s.live) break
                if (s.state == RecState.RECORDING) {
                    _level.value = (mic?.level() ?: 0f).coerceIn(0f, 1f)
                    // The heartbeat on disk every two seconds: a kill loses at most that.
                    if (++n % 20 == 0) lock.withLock { _current.value?.let { if (it.state == RecState.RECORDING) put(RecorderMachine.heartbeat(it, now())) } }
                }
            }
        }
    }

    /**
     * Upload [id] once (RecordingUploadWork calls this). Network errors, 5xx, 429 and an ended
     * session are retried; a refusal (400, 403, 404, 409, 413, 415, 422) is final and keeps the
     * audio on the phone so it can be tried again or removed.
     */
    suspend fun uploadNow(id: String): UploadOutcome = uploads.withLock {
        val s = store.load(id) ?: return UploadOutcome.FINAL
        if (s.state == RecState.UPLOADED) return UploadOutcome.DONE
        if (!s.waiting) return if (s.live) UploadOutcome.RETRY else UploadOutcome.FINAL
        val file = store.audio(s)
        if (!file.isFile || file.length() == 0L) {
            put(RecorderMachine.failed(s, AppText.get(R.string.rec_err_no_audio), final = true))
            return UploadOutcome.FINAL
        }
        put(RecorderMachine.uploading(s))
        _progress.update { it + (id to 0f) }
        try {
            val r = api().createRecording(file, s.meta, s.elapsedMs) { done, total ->
                _progress.update { it + (id to if (total > 0) done.toFloat() / total else 0f) }
            }
            put(RecorderMachine.uploaded(store.load(id) ?: s, r.connectorId, r.recordingId))
            store.dropAudio(s)
            UploadOutcome.DONE
        } catch (e: CancellationException) {
            put(RecorderMachine.failed(store.load(id) ?: s, AppText.get(R.string.rec_err_upload_interrupted), final = false))
            throw e
        } catch (e: Exception) {
            val final = e is ApiException && e.code in setOf(400, 403, 404, 409, 413, 415, 422)
            val msg = if (e is ApiException && e.code == 413) AppText.get(R.string.rec_err_too_large) else e.userMessage()
            put(RecorderMachine.failed(store.load(id) ?: s, msg, final))
            if (final) UploadOutcome.FINAL else UploadOutcome.RETRY
        } finally {
            _progress.update { it - id }
        }
    }
}
