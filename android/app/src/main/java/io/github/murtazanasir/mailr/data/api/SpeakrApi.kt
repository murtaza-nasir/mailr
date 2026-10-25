package io.github.murtazanasir.mailr.data.api

import io.github.murtazanasir.mailr.util.catching

import io.github.murtazanasir.mailr.i18n.AppText
import io.github.murtazanasir.mailr.R
import kotlinx.serialization.SerialName
import kotlinx.serialization.Serializable
import kotlinx.serialization.encodeToString
import kotlinx.serialization.json.JsonArray
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.buildJsonObject
import kotlinx.serialization.json.put
import kotlinx.serialization.json.JsonElement
import kotlinx.serialization.json.JsonPrimitive
import kotlinx.serialization.json.contentOrNull
import kotlinx.serialization.json.longOrNull
import io.github.murtazanasir.mailr.data.recorder.RecordingMeta
import okhttp3.MediaType.Companion.toMediaType
import okhttp3.MultipartBody
import okhttp3.Request
import okhttp3.RequestBody
import okio.BufferedSink
import okio.source
import java.io.File

// Speakr recordings through mailr's own routes (/api/v1/speakr). The app never calls Speakr: content comes through mailr, and "Open in Speakr" is
// the deep link in mailr's answer, opened in the browser. Nothing here runs unless the user turns
// on Settings → Recordings (Speakr) and the server reports a Speakr connector.

@Serializable
data class SpeakrConnectorInfo(
    val id: Long,
    val name: String = "",
    @SerialName("base_url") val baseUrl: String = "",
    val status: String? = null,
    @SerialName("data_class") val dataClass: String? = null,
)

/** GET /speakr/status: whether the user has a Speakr connector. */
@Serializable
data class SpeakrStatus(val connected: Boolean = false, val connectors: List<SpeakrConnectorInfo> = emptyList())

@Serializable
data class RecordingLink(
    val id: Long,
    @SerialName("connector_id") val connectorId: Long,
    @SerialName("recording_id") val recordingId: Long,
    val kind: String,
    val target: String = "",
    val occurrence: String? = null,
    /** suggested | confirmed | dismissed */
    val state: String = "confirmed",
    val score: Double? = null,
    val source: String = "",
    val title: String? = null,
    val start: String? = null,
    @SerialName("conversation_id") val conversationId: Long? = null,
)

/** One turn of a transcript; [t] is seconds from the start. */
@Serializable
data class Segment(val t: Double? = null, val end: Double? = null, val speaker: String = "", val text: String = "")

/** An event at the time of a recording that it can be linked to. */
@Serializable
data class RecordingCandidate(
    val uid: String,
    val occurrence: String = "",
    val summary: String = "",
    val start: String? = null,
    val end: String? = null,
    val score: Double = 0.0,
)

@Serializable
data class Recording(
    @SerialName("connector_id") val connectorId: Long,
    @SerialName("recording_id") val recordingId: Long,
    val title: String = "",
    @SerialName("meeting_date") val meetingDate: String? = null,
    @SerialName("created_at") val createdAt: String? = null,
    @SerialName("duration_s") val durationS: Double? = null,
    val participants: List<String> = emptyList(),
    val tags: List<String> = emptyList(),
    val folder: String? = null,
    val status: String = "completed",
    @SerialName("has_transcript") val hasTranscript: Boolean = false,
    @SerialName("has_summary") val hasSummary: Boolean = false,
    @SerialName("local_only") val localOnly: Boolean = false,
    /** Speakr's deep link to the recording, without ?t=. */
    val url: String = "",
    val summary: String? = null,
    val notes: String? = null,
    val links: List<RecordingLink> = emptyList(),
    val candidates: List<RecordingCandidate> = emptyList(),
    /** Parts Speakr could not give now, with the reason. */
    val unavailable: Map<String, String> = emptyMap(),
    @SerialName("summary_excerpt") val summaryExcerpt: String? = null,
    /** The event section: the link to this event (suggested or confirmed). */
    val link: RecordingLink? = null,
    /** The reader card: Speakr reports the transcription failed, or it is still processing. */
    val failed: Boolean = false,
    val processing: Boolean = false,
    /** "Last time": the first line of the summary, fetched when shown. */
    @SerialName("summary_line") val summaryLine: String? = null,
) {
    val displayTitle: String get() = title.ifBlank { AppText.get(R.string.api_recording_untitled) }
}

@Serializable
data class Transcript(
    val segments: List<Segment> = emptyList(),
    val truncated: Boolean = false,
    @SerialName("total_segments") val totalSegments: Int = 0,
    val url: String = "",
)

@Serializable
data class ActionItem(
    val text: String,
    val owner: String = "",
    val mine: Boolean = false,
    val due: String? = null,
    val t: Double? = null,
    val checked: Boolean = true,
    @SerialName("already_added") val alreadyAdded: Boolean = false,
    val url: String = "",
)

@Serializable
data class ActionItems(val items: List<ActionItem> = emptyList(), val note: String? = null)

@Serializable
data class CreatedTask(val uid: String = "", val summary: String = "")

@Serializable
data class TasksCreated(val created: List<CreatedTask> = emptyList(), val pending: Int = 0)

@Serializable
data class FollowUpDraft(
    @SerialName("draft_id") val draftId: Long,
    @SerialName("conversation_id") val conversationId: Long? = null,
    val to: List<String> = emptyList(),
    val reply: Boolean = false,
)

@Serializable
data class RecordingList(val connected: Boolean = true, val items: List<Recording> = emptyList())

@Serializable
data class LastTime(val connected: Boolean = false, val item: Recording? = null)

/** A Speakr folder or tag (GET /speakr/folders, /speakr/tags), with the ASR defaults it carries. */
@Serializable
data class SpeakrLabel(
    val id: Long,
    val name: String = "",
    val color: String? = null,
    @SerialName("connector_id") val connectorId: Long? = null,
    @SerialName("default_language") val defaultLanguage: String? = null,
    @SerialName("default_min_speakers") val defaultMinSpeakers: Int? = null,
    @SerialName("default_max_speakers") val defaultMaxSpeakers: Int? = null,
    @SerialName("default_hotwords") val defaultHotwords: String? = null,
    @SerialName("default_initial_prompt") val defaultInitialPrompt: String? = null,
)

/** The recording mailr created in Speakr from an upload (POST /speakr/recordings). */
data class CreatedRecording(val connectorId: Long, val recordingId: Long, val title: String, val url: String)

/** The event a recording belongs to, for follow-ups and tasks. */
data class EventRef(val uid: String, val occurrence: String?)

/** "Weekly sync 2026-10-01 14-30.aac": the title (or "Recording") and the start, for Speakr's list. */
fun recordingFileName(meta: RecordingMeta, file: File): String {
    val stamp = java.text.SimpleDateFormat("yyyy-MM-dd HH-mm", java.util.Locale.US).format(java.util.Date(file.lastModified()))
    val base = meta.title.trim().ifBlank { "Recording" }.replace(Regex("[\\\\/:*?\"<>|]"), " ").take(80)
    return "$base $stamp.aac"
}

class SpeakrApi(private val client: ApiClient) {
    suspend fun status() = client.get("speakr/status", SpeakrStatus.serializer())

    // ACT recording.view @android.recording
    suspend fun recording(cid: Long, rid: Long) =
        client.get("speakr/recordings/$cid/$rid", Recording.serializer(), mapOf("include" to "summary,notes,links"))

    suspend fun transcript(cid: Long, rid: Long) =
        client.get("speakr/recordings/$cid/$rid/transcript", Transcript.serializer(), mapOf("max_chars" to 20000))

    /** The event detail's Recording section: recordings linked to (or suggested for) an occurrence. */
    suspend fun eventRecordings(uid: String, occurrence: String?) =
        client.get("speakr/events/recordings", RecordingList.serializer(), mapOf("uid" to uid, "occurrence" to occurrence.orEmpty()))

    // ACT event.last_recording @android.calendar
    suspend fun lastTime(uid: String, occurrence: String?) =
        client.get("speakr/events/last-time", LastTime.serializer(), mapOf("uid" to uid, "occurrence" to occurrence.orEmpty()))

    /** The reader's card: the recordings a Speakr notification email points at. */
    suspend fun mailCard(messageId: Long) =
        client.get("speakr/mail-card", RecordingList.serializer(), mapOf("message_id" to messageId))

    // ACT recording.link @android.recording @android.calendar
    suspend fun link(cid: Long, rid: Long, kind: String, target: String, occurrence: String?) = client.send(
        "POST", "speakr/links", RecordingLink.serializer(), MailrJson.encodeToString(buildJsonObject {
            put("connector_id", cid); put("recording_id", rid); put("kind", kind); put("target", target)
            put("occurrence", occurrence.orEmpty())
        }))

    // ACT recording.unlink @android.recording @android.calendar
    suspend fun unlink(linkId: Long) = client.send("DELETE", "speakr/links/$linkId", RecordingLink.serializer())

    // ACT recording.follow_up @android.recording: a draft only; the server never sends it.
    suspend fun followUp(cid: Long, rid: Long, mode: String, ev: EventRef?) = client.send(
        "POST", "speakr/recordings/$cid/$rid/follow-up", FollowUpDraft.serializer(), MailrJson.encodeToString(buildJsonObject {
            put("mode", mode)
            if (ev != null) { put("event_uid", ev.uid); put("occurrence", ev.occurrence) }
        }))

    // ACT recording.to_tasks @android.recording: the proposed items, then the ones the user ticked.
    suspend fun actionItems(cid: Long, rid: Long) =
        client.send("POST", "speakr/recordings/$cid/$rid/action-items", ActionItems.serializer(), "{}")

    suspend fun createTasks(cid: Long, rid: Long, items: List<ActionItem>, ev: EventRef?) = client.send(
        "POST", "speakr/recordings/$cid/$rid/tasks", TasksCreated.serializer(), MailrJson.encodeToString(buildJsonObject {
            put("items", JsonArray(items.map { it.toBody() }))
            if (ev != null) { put("event_uid", ev.uid); put("occurrence", ev.occurrence) }
        }))

    // ---- New recording: Speakr's folders and tags, and the upload ----

    /** A list answer: a bare array, or an object holding it under `items`, `folders` or `tags`. */
    private suspend fun labels(path: String): List<SpeakrLabel> {
        val el = client.get(path, JsonElement.serializer())
        val arr = el as? JsonArray ?: (el as? JsonObject)?.let { o -> (o["items"] ?: o["folders"] ?: o["tags"]) as? JsonArray } ?: JsonArray(emptyList())
        return arr.mapNotNull { catching { MailrJson.decodeFromJsonElement(SpeakrLabel.serializer(), it) }.getOrNull() }
    }

    suspend fun folders() = labels("speakr/folders")
    suspend fun tags() = labels("speakr/tags")

    /**
     * Upload a recording made on the phone: the audio and what the user set (POST /speakr/recordings,
     * multipart). mailr uploads it to Speakr and sets the metadata on the new recording.
     * [progress] gets (bytes sent, total). ACT recording.record @android.recording
     */
    suspend fun createRecording(file: File, meta: RecordingMeta, durationMs: Long,
                                progress: (Long, Long) -> Unit = { _, _ -> }): CreatedRecording {
        val total = file.length()
        val audio = object : RequestBody() {
            override fun contentType() = "audio/aac".toMediaType()
            override fun contentLength() = total
            override fun writeTo(sink: BufferedSink) {
                file.source().use { src ->
                    var sent = 0L
                    val buf = okio.Buffer()
                    while (true) {
                        val n = src.read(buf, 64 * 1024L)
                        if (n == -1L) break
                        sink.write(buf, n)
                        sent += n
                        progress(sent, total)
                    }
                }
            }
        }
        val b = MultipartBody.Builder().setType(MultipartBody.FORM)
            .addFormDataPart("file", recordingFileName(meta, file), audio)
        fun field(k: String, v: Any?) { if (v != null && v.toString().isNotBlank()) b.addFormDataPart(k, v.toString()) }
        field("title", meta.title.trim())
        field("participants", meta.participants.joinToString(", "))
        field("meeting_date", meta.meetingDate)
        field("notes", meta.notes)
        // mailr's route takes the folder and tags by id (or name), the tags comma-separated.
        field("folder", meta.folderId)
        field("tags", meta.tagIds.joinToString(","))
        field("connector_id", meta.connectorId)
        // Speakr's transcription options, for when mailr passes them on (the route ignores unknown fields).
        field("language", meta.language)
        field("min_speakers", meta.minSpeakers)
        field("max_speakers", meta.maxSpeakers)
        field("hotwords", meta.hotwords.trim())
        field("initial_prompt", meta.initialPrompt.trim())
        field("event_uid", meta.eventUid)
        field("occurrence", meta.occurrence)
        field("duration_s", durationMs / 1000)
        field("file_last_modified", file.lastModified())
        val el = client.execute(Request.Builder().url(client.url("speakr/recordings")).post(b.build()).build(), JsonElement.serializer())
        val o = el as? JsonObject ?: throw ApiException(502, AppText.get(R.string.api_speakr_no_recording))
        val r = (o["recording"] as? JsonObject) ?: o
        fun long(k: String) = (r[k] as? JsonPrimitive)?.longOrNull
        fun str(k: String) = (r[k] as? JsonPrimitive)?.contentOrNull
        return CreatedRecording(long("connector_id") ?: throw ApiException(502, AppText.get(R.string.api_speakr_no_recording)),
            long("recording_id") ?: long("id") ?: throw ApiException(502, AppText.get(R.string.api_speakr_no_recording)),
            str("title").orEmpty(), str("url").orEmpty())
    }

    private fun ActionItem.toBody(): JsonObject = buildJsonObject {
        put("text", text); put("due", due); put("t", t)
    }
}
