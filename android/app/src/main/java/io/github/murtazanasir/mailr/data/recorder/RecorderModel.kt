package io.github.murtazanasir.mailr.data.recorder

import kotlinx.serialization.SerialName
import kotlinx.serialization.Serializable

// A recording made on the phone for Speakr (New recording). The audio goes
// to a file in the app's storage as it is recorded, and a small JSON record beside it says where
// the recording stands, so nothing is lost when the app is killed: the next start finds the record,
// stops it where the file ends and uploads it. Uploads go through mailr (POST /speakr/recordings)
// and are retried until the server takes them.

/** Where a recording stands. Only the transitions in [RecorderMachine] change it. */
enum class RecState { RECORDING, PAUSED, STOPPED, UPLOADING, UPLOADED, FAILED }

/** What the user set on the New recording screen; sent with the audio. */
@Serializable
data class RecordingMeta(
    val title: String = "",
    val participants: List<String> = emptyList(),
    /** ISO date-time with offset; the start of the recording when unset. */
    @SerialName("meeting_date") val meetingDate: String? = null,
    /** The Speakr account (one connector is the usual case; null lets mailr pick it). */
    @SerialName("connector_id") val connectorId: Long? = null,
    @SerialName("folder_id") val folderId: Long? = null,
    @SerialName("tag_ids") val tagIds: List<Long> = emptyList(),
    /** ASR language code; null lets Speakr detect it. */
    val language: String? = null,
    @SerialName("min_speakers") val minSpeakers: Int? = null,
    @SerialName("max_speakers") val maxSpeakers: Int? = null,
    val hotwords: String = "",
    @SerialName("initial_prompt") val initialPrompt: String = "",
    @SerialName("event_uid") val eventUid: String? = null,
    val occurrence: String? = null,
    @SerialName("event_title") val eventTitle: String? = null,
    /** Notes written while recording, as Markdown (Speakr's notes are Markdown). */
    val notes: String = "",
)

@Serializable
data class RecordingSession(
    val id: String,
    /** The audio file's name in the recordings directory. */
    val file: String,
    val state: RecState = RecState.RECORDING,
    @SerialName("created_at") val createdAt: Long,
    /** Recorded time before the current run (ms). */
    @SerialName("elapsed_ms") val elapsedMs: Long = 0,
    /** Wall time the current run started, while RECORDING. */
    @SerialName("run_started_at") val runStartedAt: Long? = null,
    /** The last time the recorder said it was alive (a crash keeps the audio up to here). */
    @SerialName("heartbeat_at") val heartbeatAt: Long? = null,
    val meta: RecordingMeta = RecordingMeta(),
    val attempts: Int = 0,
    val error: String? = null,
    /** True when retrying would not help (the server refused the file). */
    @SerialName("error_final") val errorFinal: Boolean = false,
    @SerialName("connector_id") val connectorId: Long? = null,
    @SerialName("recording_id") val recordingId: Long? = null,
    /** Set when the record was recovered after the app stopped while recording. */
    val recovered: Boolean = false,
) {
    /** Recorded time at [now]. */
    fun elapsedAt(now: Long): Long = elapsedMs + (if (state == RecState.RECORDING && runStartedAt != null) maxOf(0, now - runStartedAt) else 0)

    val live: Boolean get() = state == RecState.RECORDING || state == RecState.PAUSED
    val waiting: Boolean get() = state == RecState.STOPPED || state == RecState.UPLOADING || (state == RecState.FAILED && !errorFinal)
}

class IllegalTransition(from: RecState, what: String) : IllegalStateException("Cannot $what a recording that is ${from.name.lowercase()}")

/** The recorder's transitions, as pure functions of a session and the time. */
object RecorderMachine {
    fun start(id: String, file: String, meta: RecordingMeta, now: Long) =
        RecordingSession(id, file, RecState.RECORDING, createdAt = now, runStartedAt = now, heartbeatAt = now,
            meta = meta)

    fun pause(s: RecordingSession, now: Long): RecordingSession {
        if (s.state != RecState.RECORDING) throw IllegalTransition(s.state, "pause")
        return s.copy(state = RecState.PAUSED, elapsedMs = s.elapsedAt(now), runStartedAt = null, heartbeatAt = now)
    }

    fun resume(s: RecordingSession, now: Long): RecordingSession {
        if (s.state != RecState.PAUSED) throw IllegalTransition(s.state, "resume")
        return s.copy(state = RecState.RECORDING, runStartedAt = now, heartbeatAt = now)
    }

    fun stop(s: RecordingSession, now: Long): RecordingSession {
        if (!s.live) throw IllegalTransition(s.state, "stop")
        return s.copy(state = RecState.STOPPED, elapsedMs = s.elapsedAt(now), runStartedAt = null, heartbeatAt = now)
    }

    /** The recorder is alive: keep the time so a crash loses at most the time since. */
    fun heartbeat(s: RecordingSession, now: Long): RecordingSession =
        if (s.state == RecState.RECORDING) s.copy(heartbeatAt = now) else s

    /**
     * After the app stopped while this was recording: the audio file holds what was recorded up
     * to the last heartbeat, so the session stops there and waits for its upload.
     */
    fun recover(s: RecordingSession): RecordingSession {
        if (!s.live) return if (s.state == RecState.UPLOADING) s.copy(state = RecState.STOPPED) else s
        val end = s.heartbeatAt ?: s.runStartedAt ?: s.createdAt
        return s.copy(state = RecState.STOPPED, elapsedMs = s.elapsedAt(end), runStartedAt = null, recovered = true)
    }

    fun uploading(s: RecordingSession): RecordingSession {
        if (!s.waiting) throw IllegalTransition(s.state, "upload")
        return s.copy(state = RecState.UPLOADING, attempts = s.attempts + 1, error = null, errorFinal = false)
    }

    fun uploaded(s: RecordingSession, connectorId: Long, recordingId: Long) =
        s.copy(state = RecState.UPLOADED, connectorId = connectorId, recordingId = recordingId, error = null, errorFinal = false)

    /** [final]: the server refused the file (retrying would not help); otherwise it is retried. */
    fun failed(s: RecordingSession, error: String, final: Boolean) =
        s.copy(state = RecState.FAILED, error = error, errorFinal = final)

    /** "Try again" after a final failure. */
    fun retry(s: RecordingSession): RecordingSession =
        if (s.state == RecState.FAILED) s.copy(state = RecState.STOPPED, error = null, errorFinal = false) else s

    fun notes(s: RecordingSession, markdown: String) = s.copy(meta = s.meta.copy(notes = markdown))

    fun meta(s: RecordingSession, meta: RecordingMeta) = s.copy(meta = meta.copy(notes = s.meta.notes))
}
