package io.github.murtazanasir.mailr.data.api

import kotlinx.serialization.SerialName
import kotlinx.serialization.Serializable
import kotlinx.serialization.json.JsonElement
import kotlinx.serialization.json.JsonNull
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.JsonPrimitive
import kotlinx.serialization.json.buildJsonArray
import kotlinx.serialization.json.buildJsonObject

// What Ambrose remembers (backend knowledge/routes.py) and the person card
// (GET /people/{email} with `knowledge`). The same endpoints as the web's Memory page
// (frontend/src/features/settings/assistant). Unknown fields are ignored.

/** A memory as GET /assistant/memories sends it (knowledge/memory.py memory_out). */
@Serializable
data class Memory(
    val id: Long,
    @Serializable(with = SafeTextSerializer::class) val text: String = "",
    /** fact | preference | routine | commitment. */
    val kind: String = "fact",
    @SerialName("entity_id") val entityId: Long? = null,
    val entity: MemoryAbout? = null,
    /** user | agent | extracted | chat. */
    val source: String = "user",
    /** active | suggested. */
    val status: String = "active",
    val confidence: Double? = null,
    val refs: List<MemoryRef> = emptyList(),
    /** From a local-only account: used with local models only. */
    val sensitive: Boolean = false,
    val pinned: Boolean = false,
    @SerialName("expires_at") val expiresAt: String? = null,
    @SerialName("updated_at") val updatedAt: String? = null,
    /** The chat turn a memory from a chat came from, while that chat exists ("From a chat" opens it). */
    val chat: MemoryChat? = null,
    /** A suggested edit: the saved note it changes when kept (the note keeps its place). */
    val replaces: MemoryReplaced? = null,
    /** Notes merged into this one, newest first ("Merged from N notes"; never used on their own). */
    @SerialName("merged_from") val mergedFrom: List<MergedNote> = emptyList(),
    /** A tidy proposal: the other saved notes it merges (with [replaces]), or the one it retires. */
    val merges: List<MemoryReplaced> = emptyList(),
    val retires: MemoryRetired? = null,
    /** The person or topic it is about, for grouping (other wordings of a topic share a key). */
    val topic: MemoryTopic? = null,
) {
    /** The email a memory came from, if any (its Source link). */
    val conversationId: Long? get() = refs.firstNotNullOfOrNull { it.conversationId }
    val messageId: Long? get() = refs.firstOrNull { it.conversationId != null }?.messageId
    /** A dated commitment's date, if any. */
    val due: String? get() = refs.firstNotNullOfOrNull { it.due }
    /** About you: no entity, or your own entry. */
    val aboutYou: Boolean get() = entity == null || entity.isSelf
}

/** A note merged into another: its earlier text, listed under the note it went into. */
@Serializable
data class MergedNote(val id: Long, @Serializable(with = SafeTextSerializer::class) val text: String = "",
                      @SerialName("updated_at") val updatedAt: String? = null)

/** GET /assistant/memories/counts: the waiting suggestions, and whether the weekly review card is due. */
@Serializable
data class ReviewCounts(val suggested: Int = 0, @SerialName("review_card") val reviewCard: Boolean = false, val week: String = "")

/** The group a note belongs to on review (server's knowledge/memory.topics): `self` is the user. */
@Serializable
data class MemoryTopic(val key: String, val name: String = "", val self: Boolean = false)

/** The saved note a tidy proposal retires, and why. */
@Serializable
data class MemoryRetired(val id: Long, @Serializable(with = SafeTextSerializer::class) val text: String = "",
                         @Serializable(with = SafeTextSerializer::class) val reason: String = "")

/** The saved note a suggested edit changes. */
@Serializable
data class MemoryReplaced(val id: Long, @Serializable(with = SafeTextSerializer::class) val text: String = "")

/** Where a memory from a chat came from: the chat and the user's message in it. */
@Serializable
data class MemoryChat(
    @SerialName("session_id") val sessionId: String,
    @SerialName("chat_message_id") val messageId: Long? = null,
)

@Serializable
data class MemoryRef(
    @SerialName("message_id") val messageId: Long? = null,
    @SerialName("conversation_id") val conversationId: Long? = null,
    /** A suggestion's subject before it is kept (a person, a topic or you). */
    val about: RefAbout? = null,
    /** A dated commitment's date (YYYY-MM-DD): Make a task gives the task that date. */
    val due: String? = null,
)

@Serializable
data class RefAbout(
    @Serializable(with = SafeTextSerializer::class) val name: String = "",
    val emails: List<String> = emptyList(),
    val kind: String? = null,
    val self: Boolean = false,
)

@Serializable
data class MemoryCounts(val active: Int = 0, val suggested: Int = 0)

@Serializable
data class MemoryList(val items: List<Memory> = emptyList(), val counts: MemoryCounts = MemoryCounts())

/** One learning pass over sent mail (knowledge/tasks.py learning_status). */
@Serializable
data class LearningRun(
    val at: String = "",
    val messages: Int = 0,
    val suggested: Int = 0,
    /** null, model_failed, cut_off, unreadable or gave_up. */
    val error: String? = null,
    /** No new suggestions: 15 are waiting for review. */
    val full: Boolean = false,
)

@Serializable
data class LearningStatus(val enabled: Boolean = false, val last: LearningRun? = null)

/** A person or topic Ambrose knows (knowledge/memory.py entity_out). */
@Serializable
data class KnownEntity(
    val id: Long,
    /** person | organization | project | topic | place. */
    val kind: String = "person",
    @Serializable(with = SafeTextSerializer::class) val name: String = "",
    val aliases: List<String> = emptyList(),
    val emails: List<String> = emptyList(),
    val domains: List<String> = emptyList(),
    @Serializable(with = SafeTextSerializer::class) val summary: String = "",
    @SerialName("is_self") val isSelf: Boolean = false,
)

@Serializable
data class KnownEntities(val items: List<KnownEntity> = emptyList())

/** GET /assistant/entities/{id}: the entry, its memories and the contacts that carry its addresses. */
@Serializable
data class EntityDetail(
    val id: Long,
    val kind: String = "person",
    @Serializable(with = SafeTextSerializer::class) val name: String = "",
    val aliases: List<String> = emptyList(),
    val emails: List<String> = emptyList(),
    val domains: List<String> = emptyList(),
    @Serializable(with = SafeTextSerializer::class) val summary: String = "",
    @SerialName("is_self") val isSelf: Boolean = false,
    val memories: List<Memory> = emptyList(),
    val contacts: List<Person> = emptyList(),
) {
    val asEntity: KnownEntity get() = KnownEntity(id, kind, name, aliases, emails, domains, summary, isSelf)
}

@Serializable
data class PersonPhone(val value: String = "", val type: String? = null)

@Serializable
data class PersonAddress(@Serializable(with = SafeTextSerializer::class) val text: String = "")

@Serializable
data class PersonGroup(val id: Long = 0, @Serializable(with = SafeTextSerializer::class) val name: String = "")

/** Ambrose's memory about a contact: the person entry linked to it, and its memories. */
@Serializable
data class PersonKnowledge(val entity: KnownEntity? = null, val memories: List<Memory> = emptyList())

/** GET /people/{email}: the contact, as the web's person card shows it. */
@Serializable
data class PersonCard(
    val email: String,
    @Serializable(with = SafeTextSerializer::class) val name: String = "",
    @Serializable(with = SafeTextSerializer::class) val organization: String = "",
    @Serializable(with = SafeTextSerializer::class) val title: String = "",
    @SerialName("sent_count") val sentCount: Int = 0,
    @SerialName("received_count") val receivedCount: Int = 0,
    @SerialName("last_seen") val lastSeen: String? = null,
    val phones: List<PersonPhone> = emptyList(),
    val addresses: List<PersonAddress> = emptyList(),
    val birthday: String = "",
    val groups: List<PersonGroup> = emptyList(),
    val knowledge: PersonKnowledge = PersonKnowledge(),
)

/** Who a new memory is about: you, a known entry, or a new person or topic (the server finds or makes it). */
sealed interface MemorySubject {
    data object You : MemorySubject
    data class Known(val id: Long) : MemorySubject
    data class New(val name: String, val kind: String = "person", val email: String? = null) : MemorySubject
}

/** The fields the add and edit sheet saves. [expires]: an ISO instant, or null. */
data class MemoryEdit(
    val text: String,
    val kind: String,
    val subject: MemorySubject,
    val pinned: Boolean = false,
    val expires: String? = null,
)

internal fun memoryBody(e: MemoryEdit, sensitive: Boolean? = null): JsonObject = buildJsonObject {
    put("text", JsonPrimitive(e.text.trim()))
    put("kind", JsonPrimitive(e.kind))
    when (val s = e.subject) {
        MemorySubject.You -> { put("entity_id", JsonNull); put("about", JsonPrimitive("me")) }
        is MemorySubject.Known -> put("entity_id", JsonPrimitive(s.id))
        is MemorySubject.New -> {
            put("entity_id", JsonNull)
            put("about", buildJsonObject {
                put("name", JsonPrimitive(s.name))
                put("kind", JsonPrimitive(s.kind))
                if (s.email != null) put("emails", buildJsonArray { add(JsonPrimitive(s.email)) })
            })
        }
    }
    put("pinned", JsonPrimitive(e.pinned))
    put("expires_at", e.expires?.let { JsonPrimitive(it) } ?: JsonNull)
    if (sensitive != null) put("sensitive", JsonPrimitive(sensitive))
}

private fun json(o: JsonObject) = MailrJson.encodeToString(JsonObject.serializer(), o)

/** Active memories (status active) or suggestions (status suggested), as the web's Memory page reads them. */
suspend fun MailrApi.memories(status: String): MemoryList =
    client.get("assistant/memories", MemoryList.serializer(), mapOf("status" to status, "limit" to 500))

suspend fun MailrApi.learningStatus(): LearningStatus = client.get("assistant/memories/learning", LearningStatus.serializer())

suspend fun MailrApi.learnNow() = client.send("POST", "assistant/memories/learn", JsonObject.serializer())

suspend fun MailrApi.acceptMemory(id: Long) = client.send("POST", "assistant/memories/$id/accept", JsonElement.serializer())

suspend fun MailrApi.rejectMemory(id: Long) = client.send("POST", "assistant/memories/$id/reject", JsonElement.serializer())

suspend fun MailrApi.memoryCounts(): ReviewCounts = client.get("assistant/memories/counts", ReviewCounts.serializer())

suspend fun MailrApi.mergeMemory(id: Long, into: Long) = client.send("POST", "assistant/memories/$id/merge", JsonElement.serializer(),
    json(buildJsonObject { put("into", JsonPrimitive(into)) }))

suspend fun MailrApi.tidyMemories() = client.send("POST", "assistant/memories/tidy", JsonElement.serializer())

suspend fun MailrApi.dismissReviewCard() = client.send("POST", "assistant/memories/review-card/dismiss", JsonElement.serializer())

suspend fun MailrApi.createMemory(e: MemoryEdit, sensitive: Boolean? = null) =
    client.send("POST", "assistant/memories", JsonElement.serializer(), json(memoryBody(e, sensitive)))

suspend fun MailrApi.editMemory(id: Long, e: MemoryEdit) =
    client.send("PATCH", "assistant/memories/$id", JsonElement.serializer(), json(memoryBody(e)))

suspend fun MailrApi.pinMemory(id: Long, pinned: Boolean) = client.send("PATCH", "assistant/memories/$id", JsonElement.serializer(),
    json(buildJsonObject { put("pinned", JsonPrimitive(pinned)) }))

suspend fun MailrApi.knownEntities(): KnownEntities = client.get("assistant/entities", KnownEntities.serializer())

suspend fun MailrApi.entityDetail(id: Long): EntityDetail = client.get("assistant/entities/$id", EntityDetail.serializer())

suspend fun MailrApi.personCard(email: String): PersonCard =
    // The client encodes the path segment; an address never holds '/' or '?'.
    client.get("people/${email.lowercase().filter { it != '/' && it != '?' }}", PersonCard.serializer())
