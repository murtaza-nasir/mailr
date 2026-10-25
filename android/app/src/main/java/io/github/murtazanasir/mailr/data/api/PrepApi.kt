package io.github.murtazanasir.mailr.data.api

import io.github.murtazanasir.mailr.i18n.AppText
import io.github.murtazanasir.mailr.R
import kotlinx.serialization.SerialName
import kotlinx.serialization.Serializable

// Meeting prep (event.prep): GET /calendar/events/{uid}/prep and POST …/prep/summary
// (backend calendar/meeting_prep.py). Unknown fields are ignored; every list defaults to empty.

@Serializable
data class PrepGuest(@Serializable(with = SafeAddressSerializer::class) val email: String = "", @Serializable(with = SafeTextSerializer::class) val name: String = "") {
    val display: String get() = name.ifBlank { email }
}

@Serializable
data class PrepTask(val uid: String = "", val summary: String = "", val due: String? = null, val why: String? = null)

@Serializable
data class PrepActionItems(val open: List<PrepTask> = emptyList(), val done: Int = 0)

@Serializable
data class PrepRecording(
    @SerialName("connector_id") val connectorId: Long = 0,
    @SerialName("recording_id") val recordingId: Long = 0,
    val title: String = "",
    @SerialName("meeting_date") val meetingDate: String? = null,
    @SerialName("created_at") val createdAt: String? = null,
    @SerialName("summary_line") val summaryLine: String? = null,
    @SerialName("action_items") val actionItems: PrepActionItems = PrepActionItems(),
)

@Serializable
data class PrepThread(
    @SerialName("conversation_id") val conversationId: Long = 0,
    val subject: String = "",
    val snippet: String = "",
    @SerialName("last_date") val lastDate: String? = null,
    @SerialName("from_me") val fromMe: Boolean = false,
)

@Serializable
data class PrepEntity(val id: Long = 0, val name: String = "", val kind: String = "", val summary: String = "")

@Serializable
data class PrepNoteText(val id: Long = 0, val text: String = "")

@Serializable
data class PrepNote(val entity: PrepEntity = PrepEntity(), val notes: List<PrepNoteText> = emptyList())

@Serializable
data class PrepAttachment(
    @SerialName("conversation_id") val conversationId: Long = 0,
    val filename: String = "",
    val date: String? = null,
    val from: String = "",
    @SerialName("from_name") val fromName: String = "",
    val direction: String = "received",
)

@Serializable
data class PrepQuestion(@SerialName("conversation_id") val conversationId: Long = 0, val from: String = "", val question: String = "")

@Serializable
data class PrepWaiting(@SerialName("conversation_id") val conversationId: Long = 0, val subject: String = "", val since: String? = null)

@Serializable
data class PrepPromise(@SerialName("conversation_id") val conversationId: Long = 0, val text: String = "")

@Serializable
data class PrepOpenItems(
    val questions: List<PrepQuestion> = emptyList(),
    @SerialName("waiting_on") val waitingOn: List<PrepWaiting> = emptyList(),
    val promised: List<PrepPromise> = emptyList(),
)

@Serializable
data class PrepEvent(val uid: String = "", val title: String = "", val guests: List<PrepGuest> = emptyList())

@Serializable
data class PrepPack(
    val event: PrepEvent = PrepEvent(),
    val recording: PrepRecording? = null,
    val threads: List<PrepThread> = emptyList(),
    val tasks: List<PrepTask> = emptyList(),
    val notes: List<PrepNote> = emptyList(),
    val attachments: List<PrepAttachment> = emptyList(),
    @SerialName("open_items") val openItems: PrepOpenItems = PrepOpenItems(),
    val hidden: Int = 0,
) {
    val empty: Boolean get() = recording == null && threads.isEmpty() && tasks.isEmpty() && notes.isEmpty() &&
        attachments.isEmpty() && openItems.questions.isEmpty() && openItems.waitingOn.isEmpty() && openItems.promised.isEmpty()

    /** The collapsed line: "Last recording · 3 threads · 1 open question · 2 tasks" (as the web shows it). */
    fun counts(): String {
        fun n(k: Int, id: Int) = if (k == 0) "" else AppText.plural(id, k, k)
        return listOf(
            if (recording != null) AppText.get(R.string.api_prep_last_recording) else "",
            n(threads.size, R.plurals.api_prep_threads),
            n(openItems.questions.size, R.plurals.api_prep_open_questions),
            n(openItems.waitingOn.size, R.plurals.api_prep_waiting_on),
            n(tasks.size, R.plurals.api_prep_tasks),
            n(notes.size, R.plurals.api_prep_notes),
            n(attachments.size, R.plurals.api_prep_files),
        ).filter { it.isNotEmpty() }.joinToString(" · ")
    }

    fun guestName(email: String): String = event.guests.firstOrNull { it.email == email }?.display ?: email
}

@Serializable
data class PrepSummary(val status: String = "", val text: String? = null, val message: String? = null)

// ACT event.prep @android.calendar @android.sheet
suspend fun CalendarApi.prep(uid: String, occurrence: String?): PrepPack =
    client.get("calendar/events/$uid/prep", PrepPack.serializer(), mapOf("occurrence" to occurrence.orEmpty()))

suspend fun CalendarApi.prepSummary(uid: String, occurrence: String?): PrepSummary =
    client.send("POST", "calendar/events/$uid/prep/summary", PrepSummary.serializer(),
        CalendarApi.json("occurrence" to occurrence.orEmpty()), idempotencyKey = null)
