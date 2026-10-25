package io.github.murtazanasir.mailr.data.agent

import io.github.murtazanasir.mailr.util.catching

import io.github.murtazanasir.mailr.ui.present.ServerText
import io.github.murtazanasir.mailr.i18n.AppText
import io.github.murtazanasir.mailr.R
import io.github.murtazanasir.mailr.data.api.ApiClient
import io.github.murtazanasir.mailr.data.api.ApiException
import io.github.murtazanasir.mailr.data.api.Approval
import io.github.murtazanasir.mailr.data.api.Citation
import io.github.murtazanasir.mailr.data.api.ComposePayload
import io.github.murtazanasir.mailr.data.api.MailrJson
import kotlinx.coroutines.channels.awaitClose
import kotlinx.coroutines.flow.Flow
import kotlinx.coroutines.flow.callbackFlow
import kotlinx.coroutines.flow.map
import kotlinx.serialization.builtins.ListSerializer
import kotlinx.serialization.encodeToString
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.booleanOrNull
import kotlinx.serialization.json.buildJsonObject
import kotlinx.serialization.json.contentOrNull
import kotlinx.serialization.json.decodeFromJsonElement
import kotlinx.serialization.json.intOrNull
import kotlinx.serialization.json.jsonPrimitive
import kotlinx.serialization.json.longOrNull
import kotlinx.serialization.json.put
import okhttp3.Request
import okhttp3.RequestBody.Companion.toRequestBody
import okhttp3.Response
import okhttp3.sse.EventSource
import okhttp3.sse.EventSourceListener
import okhttp3.sse.EventSources
import java.util.concurrent.TimeUnit

/**
 * Events from `POST /agent/chat` (server-side chat sessions, streamed as SSE). Text and steps carry
 * `segment`, their position in the turn's timeline (ui/assistant/ChatTimeline.kt); a session event
 * with [Session.continuation] starts the continuation of the last turn after an approval.
 */
sealed interface AgentEvent {
    /**
     * `memoryIds`: the notes that went into the prompt (the "Used memories" line); [userMessageId]: the
     * stored id of the message this turn answers (`/retry` runs the turn again from it).
     */
    data class Session(val id: String, val title: String?, val continuation: Boolean = false, val segment: Int? = null,
                       val memoryIds: List<Long> = emptyList(), val userMessageId: Long? = null) : AgentEvent
    /** The short title made for a new chat while its first answer was written. */
    data class Title(val title: String) : AgentEvent
    data class Token(val text: String, val segment: Int = 0) : AgentEvent
    /** A citation the streamed text is about to link to; the server sends it before that text. */
    data class Cite(val citation: Citation, val segment: Int = 0) : AgentEvent
    /** Text the model wrote before calling tools, finalised (with citations); it stays where it is. */
    data class Segment(val segment: Int, val content: String, val citations: List<Citation>) : AgentEvent
    /** [email]: the `email` argument of the step (a person lookup's address, for its card). */
    data class StepStarted(val id: String, val name: String, val label: String, val icon: String? = null,
                           val segment: Int = 0, val email: String? = null) : AgentEvent
    /** [repeatOf]: the step this one repeated with the same arguments; it was not run again. */
    data class StepDone(val id: String, val name: String, val label: String, val ok: Boolean, val summary: String,
                        val count: Int?, val ms: Long?, val icon: String? = null, val segment: Int = 0,
                        val repeatOf: String? = null, val email: String? = null) : AgentEvent
    data class Draft(val draftId: Long?, val payload: ComposePayload?) : AgentEvent
    /** [approval]: the card with its display model and outcome (null from an older server). */
    data class ApprovalRequired(val approvalId: Long, val title: String, val lines: List<String>, val segment: Int = 0,
                                val approval: Approval? = null) : AgentEvent
    /** A decided card, to change in place ("Approved · moved 6 messages to Trash"). */
    data class ApprovalUpdate(val approval: Approval) : AgentEvent
    /** A bulk review started (review_mail): its progress line shows in this turn, then its review card. */
    data class Review(val reviewId: Long) : AgentEvent
    data class Final(val content: String, val citations: List<Citation>, val segment: Int = 0) : AgentEvent
    /** `memoryIds`: the notes the turn used in the end, the prompt's plus any the recall tool returned. */
    data class Done(val stopped: Boolean, val memoryIds: List<Long> = emptyList()) : AgentEvent
    /**
     * [code]: the server's error code ("no_access": the assistant is off for this user);
     * [status]: the HTTP status when the request itself failed (403 also means no access).
     */
    data class Error(val message: String, val code: String? = null, val status: Int? = null) : AgentEvent
}

/** One event of a turn with its position in the run's buffer (`seq`; null on an error the request itself got). */
data class Streamed(val seq: Int?, val event: AgentEvent)

/**
 * A chat in the list of earlier chats (GET /agent/sessions): its title (the first message, or one the
 * user gave: [renamed]), when it last changed, and [preview], the start of its latest answer (null from
 * an older server). A search result (GET /agent/sessions?q=) also says where its newest hit is
 * ([match]: title, question or answer) and carries the text around it as [snippetParts], the hits
 * marked; [titleParts] when the title matched.
 */
@kotlinx.serialization.Serializable
data class ChatSummary(
    val id: String,
    val title: String = "",
    @kotlinx.serialization.SerialName("updated_at") val updatedAt: String = "",
    val preview: String? = null,
    val sensitive: Boolean = false,
    val renamed: Boolean = false,
    val match: String? = null,
    val snippet: String? = null,
    @kotlinx.serialization.SerialName("snippet_parts") val snippetParts: List<TextPart>? = null,
    @kotlinx.serialization.SerialName("title_parts") val titleParts: List<TextPart>? = null,
)

/** A piece of a search result's text: plain, or a hit. */
@kotlinx.serialization.Serializable
data class TextPart(val text: String, val hit: Boolean = false)

/** PATCH /agent/sessions/{id}: the chat's title now, and what it was (for Undo). */
@kotlinx.serialization.Serializable
data class RenameResult(
    val id: String,
    val title: String,
    val renamed: Boolean,
    val previous: PreviousTitle,
)

@kotlinx.serialization.Serializable
data class PreviousTitle(val title: String, val renamed: Boolean)

/**
 * "Ask assistant" from an event, a task or a Speakr recording: the item
 * goes at the top of each turn's `context`, without a conversation, and the server keeps it on the
 * chat session. A local-only recording reaches the model by its id only (agent/screen.py).
 */
sealed interface AskAbout {
    val title: String

    /** [occurrence]: the canonical occurrence string, or null for the series or a single event. */
    data class Event(val uid: String, val occurrence: String?, override val title: String) : AskAbout
    data class Task(val uid: String, override val title: String) : AskAbout
    /** ACT recording.ask @android.recording: sent as `focused_recording` {connector_id, recording_id}. */
    data class Recording(val connectorId: Long, val recordingId: Long, override val title: String) : AskAbout
    /** A conversation in the reader: sent as `conversation_id`, as the reader's own Ask does. */
    data class Conversation(val id: Long, override val title: String) : AskAbout
    /**
     * ACT agent.ask_about @android.approvals: an approval card, sent as `focused_approval` {id}. The assistant explains
     * it; only the card's buttons decide it.
     */
    data class Approval(val id: Long, override val title: String) : AskAbout
    /** ACT agent.ask_about @android.notes: a note, list or table, sent as `focused_note` {id} (agent/screen.py). */
    data class Note(val id: String, override val title: String, val kind: String = "note") : AskAbout
}

class AgentClient(private val client: ApiClient) {

    /** The turn's events without their positions (callers that do not re-attach). */
    fun chat(message: String, sessionId: String?, conversationId: Long?, resumeApprovalId: Long? = null,
             about: AskAbout? = null): Flow<AgentEvent> =
        turn(message, sessionId, conversationId, resumeApprovalId, about).map { it.event }

    /**
     * A new turn (POST /agent/chat) as [Streamed] events. The flow ends without an error when the
     * connection drops mid-answer: the turn runs on at the server, and [follow] re-attaches to it.
     * An HTTP error answer (403, 409, 5xx) arrives as an [AgentEvent.Error] with its status.
     * [rerunFrom]: run the turn again from that stored user message (`rerun_from`, the web's Retry).
     * [localOnly]: `/local`, the chat stays on the local model from this message on (`local_only`).
     */
    fun turn(message: String, sessionId: String?, conversationId: Long?, resumeApprovalId: Long? = null,
             about: AskAbout? = null, rerunFrom: Long? = null, localOnly: Boolean = false): Flow<Streamed> {
        val body = chatBody(message, sessionId, conversationId, resumeApprovalId, about, rerunFrom, localOnly)
        return stream(Request.Builder().url(client.url("agent/chat")).header("Accept", "text/event-stream")
            .post(MailrJson.encodeToString(body).toRequestBody(ApiClient.JSON_TYPE)).build(), failAsError = true)
    }

    /**
     * Re-attach to a turn that is still running, or finished a few minutes ago
     * (GET /agent/chat/{id}/stream?after=<seq>): the events after [after]. The flow fails with an
     * [ApiException] when the server answers with an error (404: no run in that process); it ends
     * quietly when the connection drops again.
     */
    fun follow(sessionId: String, after: Int): Flow<Streamed> =
        stream(Request.Builder().url(client.url("agent/chat/$sessionId/stream", mapOf("after" to after)))
            .header("Accept", "text/event-stream").get().build(), failAsError = false)

    private fun stream(req: Request, failAsError: Boolean): Flow<Streamed> = callbackFlow {
        val http = client.http.newBuilder().readTimeout(5, TimeUnit.MINUTES).build()
        val source = EventSources.createFactory(http).newEventSource(req, object : EventSourceListener() {
            override fun onEvent(eventSource: EventSource, id: String?, type: String?, data: String) {
                parse(type, data)?.let { trySend(Streamed(seqOf(data), it)) }
            }

            override fun onClosed(eventSource: EventSource) {
                close()
            }

            override fun onFailure(eventSource: EventSource, t: Throwable?, response: Response?) {
                if (response != null && !response.isSuccessful) {
                    val text = catching { response.body?.string().orEmpty() }.getOrDefault("")
                    val msg = ApiClient.detailOf(text) ?: AppText.get(R.string.asst_unavailable_code, io.github.murtazanasir.mailr.i18n.AssistantName.now(), response.code)
                    if (failAsError) {
                        trySend(Streamed(null, AgentEvent.Error(msg, status = response.code)))
                        close()
                    } else close(ApiException(response.code, msg, text))
                    return
                }
                // The connection dropped (network change, the app in the background, a server restart):
                // the flow ends, and the caller re-attaches or reloads the chat.
                close()
            }
        })
        awaitClose { source.cancel() }
    }

    /** The chats of this user, newest first (GET /agent/sessions). */
    suspend fun sessions(limit: Int = 100): List<ChatSummary> =
        client.get("agent/sessions", ListSerializer(ChatSummary.serializer()), mapOf("limit" to limit))

    /** The chats whose title, questions or answers match [q], newest match first (GET /agent/sessions?q=). */
    suspend fun searchSessions(q: String, limit: Int = 50): List<ChatSummary> =
        client.get("agent/sessions", ListSerializer(ChatSummary.serializer()), mapOf("limit" to limit, "q" to q))

    /** Renames a chat (PATCH /agent/sessions/{id}); an empty [title] gives back the first question. */
    suspend fun renameSession(sessionId: String, title: String): RenameResult =
        client.send("PATCH", "agent/sessions/$sessionId", RenameResult.serializer(),
            MailrJson.encodeToString(buildJsonObject { put("title", title) }))

    /**
     * ACT agent.chat_retitle @android.assistant: `/retitle`, a new short title from the chat's first
     * question (POST /agent/sessions/{id}/retitle); the answer has the shape of a rename's.
     */
    suspend fun retitle(sessionId: String): RenameResult =
        client.send("POST", "agent/sessions/$sessionId/retitle", RenameResult.serializer())

    /** The chat's slash commands in picker order, in the user's language (GET /agent/commands). */
    suspend fun commands(): List<io.github.murtazanasir.mailr.ui.assistant.ChatCommand> =
        client.get("agent/commands", io.github.murtazanasir.mailr.ui.assistant.CommandList.serializer()).items

    /** The user's skills for the picker (GET /assistant/skills). */
    suspend fun skills(): List<io.github.murtazanasir.mailr.ui.assistant.SkillLite> =
        client.get("assistant/skills", io.github.murtazanasir.mailr.ui.assistant.SkillList.serializer()).items

    /**
     * A filter for future mail (POST /rules, as the filter editor makes one): a review card's "Always …".
     * The new filter's id (`id`, or `rule.id`), or null when the answer names none.
     */
    suspend fun createRule(body: JsonObject): Long? {
        val r = client.send("POST", "rules", JsonObject.serializer(), MailrJson.encodeToString(body))
        return (r["id"] ?: (r["rule"] as? JsonObject)?.get("id"))?.let { (it as? kotlinx.serialization.json.JsonPrimitive)?.longOrNull }
    }

    /** Deletes a filter (DELETE /rules/{id}): the Undo of a review card's "Always …". */
    suspend fun deleteRule(id: Long) {
        client.send("DELETE", "rules/$id", JsonObject.serializer())
    }

    /** Deletes a chat and its messages for good (DELETE /agent/sessions/{id}). */
    suspend fun deleteSession(sessionId: String) {
        client.send("DELETE", "agent/sessions/$sessionId", JsonObject.serializer())
    }

    companion object {
        /** The JSON of a turn: `context` carries the conversation and/or the event or task asked about. */
        fun chatBody(message: String, sessionId: String?, conversationId: Long?, resumeApprovalId: Long?, about: AskAbout?,
                     rerunFrom: Long? = null, localOnly: Boolean = false): JsonObject =
            buildJsonObject {
                if (resumeApprovalId == null && rerunFrom == null) put("message", message)
                if (localOnly) put("local_only", true)
                if (rerunFrom != null) put("rerun_from", rerunFrom)
                if (sessionId != null) put("session_id", sessionId)
                if (resumeApprovalId != null) put("resume_approval_id", resumeApprovalId)
                if (conversationId != null || about != null) put("context", buildJsonObject {
                    val conv = conversationId ?: (about as? AskAbout.Conversation)?.id
                    if (conv != null) put("conversation_id", conv)
                    when (about) {
                        is AskAbout.Event -> put("focused_event", buildJsonObject {
                            put("uid", about.uid)
                            put("occurrence", about.occurrence)
                        })
                        is AskAbout.Task -> put("focused_task", about.uid)
                        is AskAbout.Recording -> put("focused_recording", buildJsonObject {
                            put("connector_id", about.connectorId)
                            put("recording_id", about.recordingId)
                        })
                        is AskAbout.Approval -> put("focused_approval", buildJsonObject { put("id", about.id) })
                        is AskAbout.Note -> if (about.kind == "table") put("table", buildJsonObject { put("id", about.id) })
                            else put("focused_note", buildJsonObject { put("id", about.id) })
                        is AskAbout.Conversation, null -> Unit
                    }
                })
            }
    }

    /** A chat as stored (GET /agent/sessions/{id}), to show it again (ui/assistant/SessionReplay.kt). */
    suspend fun session(sessionId: String): JsonObject = client.get("agent/sessions/$sessionId", JsonObject.serializer())

    /** A bulk review's progress (GET /agent/reviews/{id}), for the chat's progress line. */
    suspend fun review(reviewId: Long): io.github.murtazanasir.mailr.data.api.ReviewState =
        client.get("agent/reviews/$reviewId", io.github.murtazanasir.mailr.data.api.ReviewState.serializer())

    /** Stop a review after the batch in progress, or continue a stopped or failed one (POST …/stop, …/resume). */
    suspend fun reviewAction(reviewId: Long, stop: Boolean): io.github.murtazanasir.mailr.data.api.ReviewState =
        client.send("POST", "agent/reviews/$reviewId/${if (stop) "stop" else "resume"}", io.github.murtazanasir.mailr.data.api.ReviewState.serializer())

    suspend fun stop(sessionId: String) {
        catching { client.send("POST", "agent/chat/$sessionId/stop", JsonObject.serializer(), idempotencyKey = null) }
    }

    private fun parse(type: String?, data: String): AgentEvent? {
        val o = catching { MailrJson.parseToJsonElement(data) as? JsonObject }.getOrNull() ?: return null
        fun s(k: String) = o[k]?.jsonPrimitive?.contentOrNull
        val seg = o["segment"]?.let { catching { it.jsonPrimitive.intOrNull }.getOrNull() } ?: 0
        fun cites() = catching {
            MailrJson.decodeFromJsonElement(ListSerializer(Citation.serializer()), o["citations"] ?: return@catching emptyList())
        }.getOrDefault(emptyList())
        // A step's words in the app's language (ui/present/ServerText.kt), else the server's English.
        fun label() = ServerText.text(o["label_msg"], s("label") ?: s("name").orEmpty())
        fun card(k: String) = (o[k] as? JsonObject)?.let { catching { MailrJson.decodeFromJsonElement(Approval.serializer(), it) }.getOrNull() }
        return when (type ?: s("type")) {
            "session" -> AgentEvent.Session(s("session_id") ?: return null, s("title"),
                o["continuation"]?.jsonPrimitive?.booleanOrNull ?: false, o["segment"]?.jsonPrimitive?.intOrNull,
                (o["memory_ids"] as? kotlinx.serialization.json.JsonArray)?.mapNotNull { it.jsonPrimitive.longOrNull }.orEmpty(),
                (o["user_message_id"] as? kotlinx.serialization.json.JsonPrimitive)?.longOrNull)
            "title" -> AgentEvent.Title(s("title")?.takeIf { it.isNotBlank() } ?: return null)
            "token" -> AgentEvent.Token(s("text").orEmpty(), seg)
            "cite" -> (o["citation"] as? JsonObject)?.let {
                catching { MailrJson.decodeFromJsonElement(Citation.serializer(), it) }.getOrNull()
            }?.let { AgentEvent.Cite(it, seg) }
            "segment" -> AgentEvent.Segment(seg, s("content").orEmpty(), cites())
            "tool_start" -> AgentEvent.StepStarted(s("id").orEmpty(), s("name").orEmpty(), label(),
                s("icon"), seg, stepEmail(o))
            "tool" -> AgentEvent.StepDone(s("id").orEmpty(), s("name").orEmpty(), label(),
                o["ok"]?.jsonPrimitive?.booleanOrNull ?: true, ServerText.text(o["summary_msg"], s("summary").orEmpty()),
                o["count"]?.jsonPrimitive?.intOrNull, o["ms"]?.jsonPrimitive?.longOrNull, s("icon"), seg,
                s("repeat_of")?.takeIf { it.isNotBlank() }, stepEmail(o))
            "draft" -> AgentEvent.Draft(o["draft_id"]?.jsonPrimitive?.longOrNull,
                o["payload"]?.let { catching { MailrJson.decodeFromJsonElement(ComposePayload.serializer(), it) }.getOrNull() })
            "approval_required" -> AgentEvent.ApprovalRequired(o["approval_id"]?.jsonPrimitive?.longOrNull ?: return null,
                (o["preview"] as? JsonObject)?.get("title")?.jsonPrimitive?.contentOrNull ?: s("title") ?: AppText.get(R.string.asst_needs_ok),
                catching {
                    MailrJson.decodeFromJsonElement(ListSerializer(kotlinx.serialization.serializer<String>()),
                        (o["preview"] as? JsonObject)?.get("lines") ?: return@catching emptyList())
                }.getOrDefault(emptyList()), seg, card("approval"))
            "approval_update" -> card("approval")?.let { AgentEvent.ApprovalUpdate(it) }
            "review" -> o["review_id"]?.jsonPrimitive?.longOrNull?.let { AgentEvent.Review(it) }
            "final" -> AgentEvent.Final(s("content").orEmpty(), cites(), seg)
            "done" -> AgentEvent.Done(o["stopped"]?.jsonPrimitive?.booleanOrNull ?: false,
                (o["memory_ids"] as? kotlinx.serialization.json.JsonArray)?.mapNotNull { it.jsonPrimitive.longOrNull }.orEmpty())
            "error" -> AgentEvent.Error(s("detail") ?: s("message") ?: AppText.get(R.string.asst_failed), s("code"))
            else -> null
        }
    }
}

/** A step's `args.email` when it holds an address (get_person: the person card opens on it). */
internal fun stepEmail(o: JsonObject): String? =
    ((o["args"] as? JsonObject)?.get("email") as? kotlinx.serialization.json.JsonPrimitive)?.contentOrNull
        ?.trim()?.lowercase()?.takeIf { it.contains('@') }

/** The `seq` a streamed event carries (its position in the run's buffer), or null. */
internal fun seqOf(data: String): Int? = catching {
    ((MailrJson.parseToJsonElement(data) as? JsonObject)?.get("seq") as? kotlinx.serialization.json.JsonPrimitive)?.intOrNull
}.getOrNull()
