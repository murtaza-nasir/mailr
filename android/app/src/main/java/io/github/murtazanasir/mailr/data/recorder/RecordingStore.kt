package io.github.murtazanasir.mailr.data.recorder

import io.github.murtazanasir.mailr.util.catching

import io.github.murtazanasir.mailr.data.api.MailrJson
import java.io.File

/**
 * Recordings on disk: `<dir>/<id>.aac` holds the audio and `<id>.json` the [RecordingSession].
 * The JSON is written to a temporary file and renamed, so a kill mid-write leaves the old record.
 */
class RecordingStore(val dir: File) {
    init {
        dir.mkdirs()
    }

    fun audio(s: RecordingSession): File = File(dir, s.file)

    fun save(s: RecordingSession): RecordingSession {
        val tmp = File(dir, "${s.id}.json.tmp")
        tmp.writeText(MailrJson.encodeToString(RecordingSession.serializer(), s))
        if (!tmp.renameTo(File(dir, "${s.id}.json"))) {
            File(dir, "${s.id}.json").writeText(tmp.readText())
            tmp.delete()
        }
        return s
    }

    fun load(id: String): RecordingSession? = File(dir, "$id.json").takeIf { it.isFile }?.let { f ->
        catching { MailrJson.decodeFromString(RecordingSession.serializer(), f.readText()) }.getOrNull()
    }

    /** Every record, newest first. */
    fun all(): List<RecordingSession> =
        (dir.listFiles { f -> f.name.endsWith(".json") } ?: emptyArray())
            .mapNotNull { f -> catching { MailrJson.decodeFromString(RecordingSession.serializer(), f.readText()) }.getOrNull() }
            .sortedByDescending { it.createdAt }

    /** Drop the audio of an uploaded recording (the record stays, for its link). */
    fun dropAudio(s: RecordingSession) {
        audio(s).delete()
    }

    fun delete(s: RecordingSession) {
        audio(s).delete()
        File(dir, "${s.id}.json").delete()
    }

    /** Uploaded records older than [keepMs] are forgotten. */
    fun prune(now: Long, keepMs: Long = 14L * 86_400_000) {
        all().filter { it.state == RecState.UPLOADED && now - it.createdAt > keepMs }.forEach(::delete)
    }
}
