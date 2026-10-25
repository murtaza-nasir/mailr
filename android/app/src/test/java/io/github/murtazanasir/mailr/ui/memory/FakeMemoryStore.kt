package io.github.murtazanasir.mailr.ui.memory

import io.github.murtazanasir.mailr.data.api.EntityDetail
import io.github.murtazanasir.mailr.data.api.KnownEntity
import io.github.murtazanasir.mailr.data.api.LearningRun
import io.github.murtazanasir.mailr.data.api.LearningStatus
import io.github.murtazanasir.mailr.data.api.Memory
import io.github.murtazanasir.mailr.data.api.MemoryAbout
import io.github.murtazanasir.mailr.data.api.MemoryChat
import io.github.murtazanasir.mailr.data.api.MemoryCounts
import io.github.murtazanasir.mailr.data.api.MemoryEdit
import io.github.murtazanasir.mailr.data.api.MemoryList
import io.github.murtazanasir.mailr.data.api.MemoryRef
import io.github.murtazanasir.mailr.data.api.MemorySubject
import io.github.murtazanasir.mailr.data.api.Person
import io.github.murtazanasir.mailr.data.api.PersonCard
import io.github.murtazanasir.mailr.data.api.PersonGroup
import io.github.murtazanasir.mailr.data.api.PersonKnowledge
import io.github.murtazanasir.mailr.data.api.PersonPhone
import io.github.murtazanasir.mailr.data.api.RefAbout
import java.io.IOException
import java.util.concurrent.CopyOnWriteArrayList

/**
 * The Memory page's data in memory (demo names only): you, Priya (a contact), Theo (a person with no
 * mail) and the Lumen Coffee project. Records every call, so tests can check what the page sent.
 */
class FakeMemoryStore(
    var failWith: Exception? = null,
    var learningStatus: LearningStatus = LearningStatus(true, LearningRun("2026-10-02T11:07:00-05:00", 12, 2)),
) : MemoryStore {
    val calls = CopyOnWriteArrayList<String>()
    val saved = CopyOnWriteArrayList<Pair<Long?, MemoryEdit>>()
    val restored = CopyOnWriteArrayList<Memory>()
    val tasks = CopyOnWriteArrayList<Memory>()
    private var next = 100L

    val you = MemoryAbout(1, "Alex Rivera", "person", isSelf = true)
    val priya = MemoryAbout(2, "Priya Shah", "person")
    val theo = MemoryAbout(3, "Theo Martin", "person")
    val lumen = MemoryAbout(4, "Lumen Coffee rebrand", "project")

    val active = CopyOnWriteArrayList(listOf(
        Memory(11, "You prefer short replies", "preference", 1, you, source = "user", pinned = true, updatedAt = "2026-09-29T09:00:00-05:00"),
        Memory(12, "You teach Tuesday evenings", "routine", 1, you, source = "extracted", updatedAt = "2026-09-29T09:00:00-05:00"),
        Memory(13, "Priya runs the Thursday design review", "fact", 2, priya, source = "extracted",
            refs = listOf(MemoryRef(501, 3066)), updatedAt = "2026-09-30T09:00:00-05:00"),
        Memory(14, "You promised Priya the brand guide by Friday", "commitment", 2, priya, source = "chat",
            expiresAt = "2026-10-09T23:59:00-05:00", updatedAt = "2026-10-01T09:00:00-05:00"),
        Memory(15, "Theo prefers calls to email", "preference", 3, theo, source = "agent", sensitive = true, updatedAt = "2026-10-01T09:00:00-05:00"),
        Memory(16, "The rebrand launches on 1 November", "fact", 4, lumen, source = "extracted", updatedAt = "2026-10-01T09:00:00-05:00"),
    ))
    val suggested = CopyOnWriteArrayList(listOf(
        Memory(21, "Dana is your accountant", "fact", status = "suggested", source = "extracted", confidence = 0.86,
            refs = listOf(MemoryRef(502, 3101, RefAbout("Dana Whitfield", listOf("dana@lumencoffee.example"), "person")))),
        Memory(22, "You are vegetarian", "preference", status = "suggested", source = "chat", confidence = 0.9,
            refs = listOf(MemoryRef(about = RefAbout(self = true))), chat = MemoryChat("chat-dinner", 7002)),
    ))

    private fun check(what: String) {
        calls += what
        failWith?.let { throw it }
    }

    override suspend fun list(status: String): MemoryList {
        check("list:$status")
        val items = if (status == "suggested") suggested.toList() else active.toList()
        return MemoryList(items, MemoryCounts(active.size, suggested.size))
    }

    override suspend fun learning(): LearningStatus { check("learning"); return learningStatus }
    override suspend fun learn() = check("learn")

    override suspend fun accept(id: Long) {
        check("accept:$id")
        val m = suggested.first { it.id == id }
        suggested.remove(m)
        active += m.copy(status = "active", entityId = 1, entity = you)
    }

    override suspend fun dismiss(id: Long) {
        check("dismiss:$id")
        suggested.removeIf { it.id == id }
    }

    override suspend fun save(id: Long?, edit: MemoryEdit) {
        check("save:${id ?: "new"}")
        saved += id to edit
        val about = when (val s = edit.subject) {
            MemorySubject.You -> you
            is MemorySubject.Known -> listOf(you, priya, theo, lumen).first { it.id == s.id }
            is MemorySubject.New -> MemoryAbout(next + 1000, s.name, s.kind)
        }
        if (id == null) active += Memory(next++, edit.text, edit.kind, about.id, about, pinned = edit.pinned, expiresAt = edit.expires)
        else active.replaceAll { if (it.id == id) it.copy(text = edit.text, kind = edit.kind, entityId = about.id, entity = about, pinned = edit.pinned) else it }
    }

    override suspend fun pin(id: Long, pinned: Boolean) {
        check("pin:$id:$pinned")
        active.replaceAll { if (it.id == id) it.copy(pinned = pinned) else it }
    }

    override suspend fun delete(id: Long) {
        check("delete:$id")
        active.removeIf { it.id == id }
    }

    override suspend fun restore(m: Memory) {
        check("restore:${m.id}")
        restored += m
        active += m.copy(id = next++)
    }

    override suspend fun entities(): List<KnownEntity> {
        check("entities")
        return listOf(KnownEntity(1, "person", "Alex Rivera", isSelf = true), KnownEntity(2, "person", "Priya Shah"),
            KnownEntity(3, "person", "Theo Martin"), KnownEntity(4, "project", "Lumen Coffee rebrand"))
    }

    override suspend fun entity(id: Long): EntityDetail {
        check("entity:$id")
        val e = listOf(priya, theo, lumen, you).first { it.id == id }
        return EntityDetail(e.id, e.kind, e.name,
            aliases = if (id == 4L) listOf("Lumen") else emptyList(),
            emails = when (id) { 2L -> listOf("priya.shah@northwind.example"); 3L -> listOf("theo.martin@example.org"); else -> emptyList() },
            domains = if (id == 4L) listOf("lumencoffee.example") else emptyList(),
            summary = if (id == 3L) "Studio neighbour, runs the print shop" else "",
            memories = active.filter { it.entityId == id },
            contacts = if (id == 2L) listOf(Person("priya.shah@northwind.example", "Priya Shah")) else emptyList())
    }

    override suspend fun person(email: String): PersonCard {
        check("person:$email")
        if (email != "priya.shah@northwind.example") throw IOException("unknown")
        return PersonCard(email, "Priya Shah", "Northwind Studio", "Art director", sentCount = 14, receivedCount = 22,
            lastSeen = "2026-10-01T16:20:00-05:00", phones = listOf(PersonPhone("+1 312 555 0142", "mobile")),
            groups = listOf(PersonGroup(7, "Studio team")),
            knowledge = PersonKnowledge(io.github.murtazanasir.mailr.data.api.KnownEntity(2, "person", "Priya Shah"), active.filter { it.entityId == 2L }))
    }

    override suspend fun makeTask(m: Memory) {
        check("task:${m.id}")
        tasks += m
    }
}
