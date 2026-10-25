package io.github.murtazanasir.mailr.ui.memory

import io.github.murtazanasir.mailr.AppGraph
import io.github.murtazanasir.mailr.data.api.EntityDetail
import io.github.murtazanasir.mailr.data.api.KnownEntity
import io.github.murtazanasir.mailr.data.api.LearningStatus
import io.github.murtazanasir.mailr.data.api.Memory
import io.github.murtazanasir.mailr.data.api.MemoryEdit
import io.github.murtazanasir.mailr.data.api.MemoryList
import io.github.murtazanasir.mailr.data.api.MemorySubject
import io.github.murtazanasir.mailr.data.api.PersonCard
import io.github.murtazanasir.mailr.data.api.TaskDraft
import io.github.murtazanasir.mailr.data.api.acceptMemory
import io.github.murtazanasir.mailr.data.api.createMemory
import io.github.murtazanasir.mailr.data.api.editMemory
import io.github.murtazanasir.mailr.data.api.entityDetail
import io.github.murtazanasir.mailr.data.api.knownEntities
import io.github.murtazanasir.mailr.data.api.learnNow
import io.github.murtazanasir.mailr.data.api.learningStatus
import io.github.murtazanasir.mailr.data.api.memories
import io.github.murtazanasir.mailr.data.api.personCard
import io.github.murtazanasir.mailr.data.api.pinMemory
import io.github.murtazanasir.mailr.data.api.rejectMemory

/**
 * What the Memory page and the person card read and change: the web's endpoints
 * (GET/POST/PATCH/DELETE /assistant/memories, /assistant/entities, GET /people/{email}), and the
 * Tasks app's own create path for a commitment's Make a task. Tests pass a fake.
 */
interface MemoryStore {
    suspend fun list(status: String): MemoryList
    suspend fun learning(): LearningStatus
    suspend fun learn()
    suspend fun accept(id: Long)
    suspend fun dismiss(id: Long)
    /** A new memory ([id] null) or a change to one. */
    suspend fun save(id: Long?, edit: MemoryEdit)
    suspend fun pin(id: Long, pinned: Boolean)
    suspend fun delete(id: Long)
    /** Undo of a delete: the same memory again (the web's Undo posts it anew). */
    suspend fun restore(m: Memory)
    suspend fun entities(): List<KnownEntity>
    suspend fun entity(id: Long): EntityDetail
    suspend fun person(email: String): PersonCard
    /** Make a task from a commitment, linked to the email it came from. */
    suspend fun makeTask(m: Memory)
}

/** "You promised Sam a quote." → "Promised Sam a quote" (the web's taskSummaryOf). */
fun taskSummaryOf(text: String): String {
    val s = text.trim().replace(Regex("^you\\s+", RegexOption.IGNORE_CASE), "").removeSuffix(".")
    return if (s.isEmpty()) text else s.replaceFirstChar { it.uppercase() }
}

/** The subject a memory is saved with when only its text, kind, pin or date change. */
fun Memory.subject(): MemorySubject = if (entity?.isSelf == true || entityId == null) MemorySubject.You else MemorySubject.Known(entityId)

class ApiMemoryStore(private val g: AppGraph) : MemoryStore {
    override suspend fun list(status: String) = g.api.memories(status)
    override suspend fun learning() = g.api.learningStatus()
    override suspend fun learn() { g.api.learnNow() }
    override suspend fun accept(id: Long) { g.api.acceptMemory(id) }
    override suspend fun dismiss(id: Long) { g.api.rejectMemory(id) }
    override suspend fun save(id: Long?, edit: MemoryEdit) {
        if (id == null) g.api.createMemory(edit) else g.api.editMemory(id, edit)
    }
    override suspend fun pin(id: Long, pinned: Boolean) { g.api.pinMemory(id, pinned) }
    override suspend fun delete(id: Long) { g.api.deleteMemory(id) }
    override suspend fun restore(m: Memory) {
        g.api.createMemory(MemoryEdit(m.text, m.kind, m.subject(), m.pinned, m.expiresAt), sensitive = m.sensitive)
    }
    override suspend fun entities() = g.api.knownEntities().items
    override suspend fun entity(id: Long) = g.api.entityDetail(id)
    override suspend fun person(email: String) = g.api.personCard(email)

    // ACT memory.make_task @android.settings: the Tasks app's create path (queued while offline), with the email linked.
    override suspend fun makeTask(m: Memory) {
        g.calendar.createTask(TaskDraft(g.calendar.newUid(), taskSummaryOf(m.text), conversationId = m.conversationId))
    }
}
