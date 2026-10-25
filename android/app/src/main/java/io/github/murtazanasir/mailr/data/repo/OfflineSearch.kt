package io.github.murtazanasir.mailr.data.repo

import io.github.murtazanasir.mailr.data.api.ConversationSummary

/**
 * Search without the server: the conversations this phone has in its list cache (every folder and
 * search opened before), filtered by the query's words and a few operators. The words must each
 * appear in the subject, the snippet or a participant's name or address (any case). Supported
 * operators: from:, to:, is:unread, is:read, is:starred, has:attachment; any other operator is
 * left out, so the results can be more than the server's. The list says they are offline results.
 */
object OfflineSearch {
    data class Query(val words: List<String>, val from: List<String>, val to: List<String>, val flags: Set<String>)

    fun parse(q: String): Query {
        val words = mutableListOf<String>()
        val from = mutableListOf<String>()
        val to = mutableListOf<String>()
        val flags = mutableSetOf<String>()
        tokens(q).forEach { t ->
            val (op, arg) = t.substringBefore(':', "") to t.substringAfter(':', "")
            when {
                t.startsWith("-") -> Unit  // a negation needs the server; left out
                op.isEmpty() || arg.isEmpty() -> words += t.lowercase()
                op == "from" -> from += arg.lowercase()
                op == "to" -> to += arg.lowercase()
                op == "is" || op == "has" -> flags += "$op:${arg.lowercase()}"
                else -> Unit  // newer_than:, label:, in: … need the server
            }
        }
        return Query(words, from, to, flags)
    }

    /** Words and "quoted phrases". */
    private fun tokens(q: String): List<String> =
        Regex("\"([^\"]+)\"|(\\S+)").findAll(q).map { it.groupValues[1].ifEmpty { it.groupValues[2] } }.filter { it.isNotBlank() }.toList()

    fun matches(c: ConversationSummary, q: Query): Boolean {
        val people = (c.participants + c.recipients).flatMap { listOfNotNull(it.name, it.email) }.map { it.lowercase() }
        val text = (listOf(c.subject, c.snippet) + c.attachmentsPreview.map { it.filename }).joinToString("\n").lowercase()
        if (q.words.any { w -> w !in text && people.none { w in it } }) return false
        if (q.from.isNotEmpty() && q.from.none { f -> c.participants.any { p -> f == "me" && p.me || f in p.email.lowercase() || f in p.name.orEmpty().lowercase() } }) return false
        if (q.to.isNotEmpty() && q.to.none { t -> c.recipients.any { p -> t in p.email.lowercase() || t in p.name.orEmpty().lowercase() } }) return false
        for (f in q.flags) when (f) {
            "is:unread" -> if (!c.unread) return false
            "is:read" -> if (c.unread) return false
            "is:starred" -> if (!c.starred) return false
            "is:important" -> if (!c.important) return false
            "has:attachment" -> if (!c.hasAttachments) return false
        }
        return true
    }

    /** The matches among [cached], one per conversation, newest first. */
    fun search(cached: List<ConversationSummary>, q: String, limit: Int = 200): List<ConversationSummary> {
        val query = parse(q)
        return cached.distinctBy { it.id }.filter { matches(it, query) }.sortedByDescending { it.lastDate.orEmpty() }.take(limit)
    }
}
