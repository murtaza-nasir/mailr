package io.github.murtazanasir.mailr.notesapp.capture

import io.github.murtazanasir.mailr.notesapp.R
import io.github.murtazanasir.mailr.i18n.AppText
import io.github.murtazanasir.mailr.notes.data.DataTable
import io.github.murtazanasir.mailr.notes.data.Note
import io.github.murtazanasir.mailr.notes.data.NotesRepository
import io.github.murtazanasir.mailr.notes.data.Section
import io.github.murtazanasir.mailr.notes.grammar.ItemGrammar
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.JsonPrimitive

/**
 * Quick capture and the share target, without the screens: which list a capture line names, what
 * it adds, and how shared text becomes a note, items or rows. Everything goes through the
 * repository, so it lands on the phone at once and reaches the server through the replay queue.
 */
object CaptureLogic {
    /** A list a capture may add to: an editable checklist that is not in Trash. */
    fun lists(notes: List<Note>, sections: Map<String, List<Section>>): List<ItemGrammar.ListInfo> =
        notes.filter { it.isList && it.canEdit && it.deletedAt == null }
            .map { ItemGrammar.ListInfo(it.id, it.title.ifBlank { AppText.get(io.github.murtazanasir.mailr.notes.R.string.notes_untitled_list) }, sections[it.id].orEmpty().map { s -> s.name }) }

    /**
     * Recent lists first (the ones opened last, then the capture default), then the rest by title,
     * for "Add to a list" in the share target.
     */
    fun ordered(lists: List<ItemGrammar.ListInfo>, recents: List<String>, defaultList: String?): List<ItemGrammar.ListInfo> {
        val rank = (recents.filter { it.startsWith("note:") }.map { it.removePrefix("note:") } + listOfNotNull(defaultList))
            .distinct().withIndex().associate { (i, id) -> id to i }
        return lists.sortedWith(compareBy<ItemGrammar.ListInfo> { rank[it.id] ?: Int.MAX_VALUE }.thenBy { it.name.lowercase() })
    }

    /** What a capture line adds: the list it names (or the default), the lines without the list token, the item count. */
    data class AddPlan(val list: ItemGrammar.ListRef?, val lines: String, val items: List<ItemGrammar.Item>, val newSections: List<String>)

    /** The grammar in capture mode (mode `capture`): a `#list` token picks the list. */
    fun plan(input: String, lists: List<ItemGrammar.ListInfo>, defaultList: ItemGrammar.ListInfo?): AddPlan {
        val r = ItemGrammar.parse(input, ItemGrammar.Mode.CAPTURE, lists = lists, defaultList = defaultList)
        val token = r.tokens.firstOrNull { it.kind == "list" }
        val lines = if (token == null) input else input.substring(0, token.start) + " ".repeat(token.end - token.start) + input.substring(token.end)
        return AddPlan(r.list, lines, r.items, r.newSections)
    }

    /**
     * ACT note.quick @android.notesapp: "New note…" text that starts with a `#List` token adds items to
     * that list ("#Shopping Costco: milk" puts milk in the Costco section of Shopping; an unknown name
     * makes the list); null for anything else, which stays a text note (ItemGrammar.quick).
     */
    fun quickPlan(text: String, lists: List<ItemGrammar.ListInfo>): AddPlan? =
        if (ItemGrammar.quick(text, lists) is ItemGrammar.Quick.Items) plan(text, lists, null) else null

    /** "Add 3 items to Shopping", "Add milk to Shopping", "Create the list Packing with 2 items". */
    fun summary(p: AddPlan): String {
        val what = if (p.items.size == 1) p.items[0].text else AppText.plural(R.plurals.notesapp_n_items, p.items.size, p.items.size)
        val list = p.list ?: return if (p.items.isEmpty()) "" else AppText.get(R.string.notesapp_summary_choose_list, what)
        return AppText.get(if (list.create) R.string.notesapp_summary_create_list else R.string.notesapp_summary_add, what, list.name)
    }

    /**
     * ACT note.add_items @android.notesapp, ACT checkitem.add @android.notesapp: the plan's items go to their list (one undo for
     * the batch on the server); a list named with `#` that does not exist yet is created with them. The
     * id of the list they went to, or null when there was nothing to add.
     */
    suspend fun add(repo: NotesRepository, p: AddPlan, sectionId: String? = null): String? {
        if (p.items.isEmpty() && p.newSections.isEmpty()) return null
        val list = p.list ?: return null
        val listId = list.id
        if (list.create || listId == null) {
            val (id, _) = repo.createNote("checklist", title = list.name, lines = p.lines)
            return id
        }
        repo.addItems(listId, p.lines, sectionId = sectionId) ?: return null
        return listId
    }

    // ---------------------------------------------------------------- shared text

    /** Shared text as a note: [title] and [body]; [link] when the text is a single web address. */
    data class Shared(val title: String, val body: String, val link: String?)

    private val URL = Regex("^https?://\\S+$", RegexOption.IGNORE_CASE)

    /**
     * The share target: a shared link becomes a note with the shared title and the address
     * (nothing is fetched); text with several lines takes its first line as the title.
     */
    fun shared(text: String?, subject: String?): Shared {
        val t = text.orEmpty().replace("\r\n", "\n").trim()
        val s = subject?.trim().orEmpty()
        if (URL.matches(t)) return Shared(s.take(500), t, t)
        // "Title https://…" as some apps share a link with its page title.
        val lastWord = t.substringAfterLast(' ', "").substringAfterLast('\n')
        if (URL.matches(lastWord) && !t.contains('\n')) {
            val title = t.removeSuffix(lastWord).trim().ifBlank { s }
            return Shared(title.take(500), lastWord, lastWord)
        }
        val lines = t.split('\n')
        if (lines.size > 1) {
            val first = lines.first().trim()
            return Shared((s.ifBlank { first }).take(500), (if (s.isBlank()) lines.drop(1).joinToString("\n") else t).trim(), null)
        }
        return Shared(s.take(500), t, null)
    }

    /** The lines of shared text as items for "Add to a list": each line or comma piece (the grammar splits). */
    fun itemsOf(text: String): String = text.replace("\r\n", "\n").trim()

    // ---------------------------------------------------------------- shared rows

    /** Tab- or comma-separated text as rows for "Add to a table" (tab when any line has one). A first line naming the columns is skipped. */
    fun rows(text: String, t: DataTable): List<List<String>> {
        val lines = text.replace("\r\n", "\n").split('\n').map { it.trimEnd() }.filter { it.isNotBlank() }
        if (lines.isEmpty()) return emptyList()
        val tab = lines.any { '\t' in it }
        val split = lines.map { l -> if (tab) l.split('\t').map { it.trim() } else splitCsv(l) }
        val names = t.columns.map { it.name.trim().lowercase() }
        val header = split.first().map { it.lowercase() }
        val body = if (header.isNotEmpty() && header.all { it in names } && split.size > 1) split.drop(1) else split
        return body.map { r -> r.take(t.columns.size.coerceAtLeast(1)) }
    }

    /** One CSV line: commas split outside double quotes; "" is a quote. */
    fun splitCsv(line: String): List<String> {
        val out = ArrayList<String>()
        val cur = StringBuilder()
        var quoted = false
        var i = 0
        while (i < line.length) {
            val ch = line[i]
            when {
                ch == '"' && quoted && line.getOrNull(i + 1) == '"' -> { cur.append('"'); i++ }
                ch == '"' -> quoted = !quoted
                ch == ',' && !quoted -> { out += cur.toString().trim(); cur.clear() }
                else -> cur.append(ch)
            }
            i++
        }
        out += cur.toString().trim()
        return out
    }

    /** ACT row.add @android.notesshare: each row's cells in the table's column order, read as typed (`parse: true`). */
    suspend fun addRows(repo: NotesRepository, t: DataTable, rows: List<List<String>>): Int {
        var n = 0
        for (r in rows) {
            val cells = JsonObject(t.columns.zip(r).filter { it.second.isNotBlank() }.associate { (c, v) -> c.id to JsonPrimitive(v) })
            if (cells.isEmpty()) continue
            repo.addRow(t.id, cells)
            n++
        }
        return n
    }
}
