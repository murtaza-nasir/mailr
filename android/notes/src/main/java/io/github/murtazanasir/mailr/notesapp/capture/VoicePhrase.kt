package io.github.murtazanasir.mailr.notesapp.capture

import io.github.murtazanasir.mailr.notesapp.R
import io.github.murtazanasir.mailr.i18n.AppText
import io.github.murtazanasir.mailr.notes.data.NotesRepository
import io.github.murtazanasir.mailr.notes.grammar.ItemGrammar
import java.util.Locale

/**
 * Voice add: a spoken phrase that names a list, read by fixed rules (no model, nothing
 * guessed). The words the phone heard on its own recognizer become a list and its items; the items
 * then go through the item grammar as typed ones do.
 *
 *   "add milk and eggs to shopping"      → Shopping: milk, eggs
 *   "put bread on the Costco list"       → Costco: bread
 *   "shopping: milk"                     → Shopping: milk (a name before a colon, known lists only)
 *   "add milk", "milk and eggs"          → from a list's widget: that list; from the capture widget,
 *                                          "add milk" asks which list and other words become a note
 *
 * A list is matched by its whole name, case and spaces ignored, with or without a trailing "list".
 * A phrase with "add … to <name>" whose name matches no list says "No list called …"; it never picks
 * a list that merely starts with the name. Anything else is not a list phrase (the capture keeps it
 * as a note).
 */
object VoicePhrase {
    sealed interface Parsed {
        data class Add(val list: ItemGrammar.ListInfo, val items: List<String>) : Parsed
        data class UnknownList(val name: String, val items: List<String>) : Parsed
        /** "add milk": items without a list (the list's own widget supplies it). */
        data class NoList(val items: List<String>) : Parsed
        data class NotAList(val text: String) : Parsed
    }

    private val VERB = Regex("^(?:please\\s+)?(?:add|put)\\s+(.+)$", RegexOption.IGNORE_CASE)
    private val PREP = Regex("\\s+(?:to|on|onto|in|into)\\s+", RegexOption.IGNORE_CASE)
    private val COLON = Regex("^([^:]{1,60}?)\\s*:\\s*(.+)$")
    private val ARTICLE = Regex("^(?:the|my|our)\\s+", RegexOption.IGNORE_CASE)
    private val LIST_WORD = Regex("\\s+list$", RegexOption.IGNORE_CASE)
    private val SPLIT = Regex("\\s*(?:,|\\band\\b|&)\\s*", RegexOption.IGNORE_CASE)
    private val SPACES = Regex("\\s+")

    private fun squash(s: String) = SPACES.replace(s.lowercase(Locale.ROOT), "")

    /** The spoken items: commas, "and" and "&" separate them. */
    fun items(text: String): List<String> = text.split(SPLIT).map { it.trim() }.filter { it.isNotEmpty() }

    /** A spoken list name against the lists: the whole name, with or without "the", "my", "our" and a trailing "list". */
    fun findList(said: String, lists: List<ItemGrammar.ListInfo>): ItemGrammar.ListInfo? {
        val bare = ARTICLE.replace(said.trim(), "")
        val forms = listOf(bare, LIST_WORD.replace(bare, "")).map(::squash).filter { it.isNotEmpty() }.distinct()
        for (f in forms) lists.firstOrNull { squash(it.name) == f }?.let { return it }
        // A list named "Shopping list" heard as "shopping".
        for (f in forms) lists.firstOrNull { squash(LIST_WORD.replace(it.name, "")) == f }?.let { return it }
        return null
    }

    /** The name as the person said it, without "the" and a trailing "list", for "No list called …". */
    private fun spoken(said: String) = LIST_WORD.replace(ARTICLE.replace(said.trim(), ""), "").trim()

    fun parse(phrase: String, lists: List<ItemGrammar.ListInfo>): Parsed {
        val text = SPACES.replace(phrase.trim().trimEnd('.', '!', '?'), " ")
        if (text.isEmpty()) return Parsed.NotAList(text)
        VERB.find(text)?.let { m ->
            val rest = m.groupValues[1]
            // Every "to/on/in" from the right: the first whose name is a list wins ("add bread to go to Shopping").
            val splits = PREP.findAll(rest).toList().reversed()
            for (s in splits) {
                val list = findList(rest.substring(s.range.last + 1), lists) ?: continue
                val its = items(rest.substring(0, s.range.first))
                if (its.isNotEmpty()) return Parsed.Add(list, its)
            }
            splits.firstOrNull()?.let { s ->
                val its = items(rest.substring(0, s.range.first))
                val name = spoken(rest.substring(s.range.last + 1))
                if (its.isNotEmpty() && name.isNotEmpty()) return Parsed.UnknownList(name, its)
            }
            val its = items(rest)
            if (its.isNotEmpty()) return Parsed.NoList(its)
        }
        COLON.find(text)?.let { m ->
            val list = findList(m.groupValues[1], lists)
            val its = items(m.groupValues[2])
            if (list != null && its.isNotEmpty()) return Parsed.Add(list, its)
        }
        return Parsed.NotAList(text)
    }

    /** "milk", "milk and eggs", "milk, eggs and bread". */
    fun join(items: List<String>): String = when (items.size) {
        0 -> ""
        1 -> items[0]
        else -> AppText.get(io.github.murtazanasir.mailr.notes.R.string.notes_names_and, items.dropLast(1).joinToString(AppText.get(io.github.murtazanasir.mailr.notes.R.string.notes_names_separator)), items.last())
    }

    /** What a voice add did: the line to show, and the text to keep as a note when it named no list. */
    data class Outcome(val message: String?, val noteText: String? = null, val listId: String? = null)

    /**
     * ACT checkitem.voice_add @android.noteswidget: the phrase's items go to the list it names (or to
     * [fallbackList], the widget's own list, when it names none), through the repository's queue, so
     * it works offline.
     */
    suspend fun apply(repo: NotesRepository, phrase: String, lists: List<ItemGrammar.ListInfo>, fallbackList: String?): Outcome {
        val target: Pair<ItemGrammar.ListInfo, List<String>> = when (val p = parse(phrase, lists)) {
            is Parsed.Add -> p.list to p.items
            is Parsed.UnknownList -> return Outcome(AppText.get(R.string.notesapp_voice_no_list_called, p.name))
            is Parsed.NoList -> lists.firstOrNull { it.id == fallbackList }?.let { it to p.items }
                ?: return Outcome(AppText.get(R.string.notesapp_voice_say_which_list))
            // From a list's own widget, plain words are its items ("milk and eggs"); elsewhere they are a note.
            is Parsed.NotAList -> lists.firstOrNull { it.id == fallbackList }?.let { it to items(p.text) }
                ?: return Outcome(null, noteText = p.text)
        }
        val (list, items) = target
        val r = repo.addItems(list.id, items.joinToString(", ")) ?: return Outcome(AppText.get(R.string.notesapp_voice_nothing_to_add))
        val dup = r.duplicates.firstOrNull()
        val added = items.size - r.duplicates.size
        return Outcome(when {
            dup != null && added <= 0 -> AppText.get(R.string.notesapp_voice_already_on, dup.replaceFirstChar { it.titlecase() }, list.name)
            else -> AppText.get(R.string.notesapp_voice_added_to, join(items.filter { it !in r.duplicates }), list.name)
        }, listId = list.id)
    }
}
