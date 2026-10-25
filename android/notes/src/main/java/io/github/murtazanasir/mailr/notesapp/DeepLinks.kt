package io.github.murtazanasir.mailr.notesapp

import io.github.murtazanasir.mailr.util.catching

import io.github.murtazanasir.mailr.ui.onboarding.parseSetupServer
import java.net.URI
import java.net.URLDecoder

/** Where a link, a shortcut or a widget sends the Notes app. */
sealed interface NotesTarget {
    data class Note(val id: String, val item: String? = null) : NotesTarget
    data class Table(val id: String) : NotesTarget
    data class Row(val tableId: String, val rowId: String) : NotesTarget
    /** A new note ("text"), list ("checklist") or table ("table"), opened at once. */
    data class New(val kind: String) : NotesTarget
    /** The server from a setup link, for the sign-in screen. */
    data class Setup(val server: String) : NotesTarget
}

/** A parsed link: its target and, for an https link, the host it named (checked against the account). */
data class NotesLink(val target: NotesTarget, val host: String? = null)

/**
 * The Notes app's addresses (ACT note.open_app @android.notesapp):
 *
 * - `mailr-notes://note/<id>`, `mailr-notes://note/<id>/item/<item>` (short: `n/<id>`, `n/<id>/i/<item>`)
 * - `mailr-notes://table/<id>`, `mailr-notes://table/<id>/row/<row>` (short: `t/<id>`, `t/<id>/r/<row>`)
 * - `mailr-notes://new?kind=text|checklist|table`; `new?kind=list&list=<id>` opens that list
 * - `mailr-notes://setup?server=https%3A%2F%2Fmail.example.com`
 * - `https://<host>/notes/n/<id>[#i-<item>]`, `https://<host>/tables/t/<id>[/r/<row>]` (the web's routes)
 *
 * Ids are letters, digits and `-_`; anything else is not a link of ours (null).
 */
object DeepLinks {
    const val SCHEME = "mailr-notes"
    private val ID = Regex("^[A-Za-z0-9_-]{1,80}$")

    fun parse(link: String?): NotesLink? {
        if (link.isNullOrBlank() || link.length > 2000) return null
        val uri = catching { URI(link) }.getOrNull() ?: return null
        val scheme = uri.scheme?.lowercase() ?: return null
        return when (scheme) {
            SCHEME -> custom(link, uri)
            "https" -> web(uri)
            else -> null
        }
    }

    private fun ok(s: String?) = s != null && ID.matches(s)

    private fun custom(link: String, uri: URI): NotesLink? {
        val host = uri.host?.lowercase() ?: uri.rawAuthority?.lowercase() ?: return null
        val parts = (uri.rawPath ?: "").split('/').filter { it.isNotEmpty() }.map { URLDecoder.decode(it, "UTF-8") }
        val target: NotesTarget = when (host) {
            "note", "n" -> {
                val id = parts.getOrNull(0)?.takeIf(::ok) ?: return null
                val item = if (parts.getOrNull(1) in setOf("item", "i")) parts.getOrNull(2)?.takeIf(::ok) ?: return null else null
                NotesTarget.Note(id, item)
            }
            "table", "t" -> {
                val id = parts.getOrNull(0)?.takeIf(::ok) ?: return null
                if (parts.getOrNull(1) in setOf("row", "r")) NotesTarget.Row(id, parts.getOrNull(2)?.takeIf(::ok) ?: return null)
                else NotesTarget.Table(id)
            }
            "new" -> {
                val q = query(uri)
                val list = q["list"]
                when (q["kind"]) {
                    null, "", "text", "note" -> NotesTarget.New("text")
                    "checklist" -> NotesTarget.New("checklist")
                    "list" -> if (list != null) NotesTarget.Note(list.takeIf(::ok) ?: return null) else NotesTarget.New("checklist")
                    "table" -> NotesTarget.New("table")
                    else -> return null
                }
            }
            "setup" -> NotesTarget.Setup(parseSetupServer(link, SCHEME) ?: return null)
            else -> return null
        }
        return NotesLink(target)
    }

    private fun web(uri: URI): NotesLink? {
        val host = uri.host?.lowercase() ?: return null
        val parts = (uri.rawPath ?: "").split('/').filter { it.isNotEmpty() }
        val target = when {
            parts.size >= 3 && parts[0] == "notes" && parts[1] == "n" && ok(parts[2]) ->
                NotesTarget.Note(parts[2], uri.rawFragment?.removePrefix("i-")?.takeIf { uri.rawFragment.startsWith("i-") && ok(it) })
            parts.size >= 3 && parts[0] == "tables" && parts[1] == "t" && ok(parts[2]) ->
                if (parts.getOrNull(3) == "r" && ok(parts.getOrNull(4))) NotesTarget.Row(parts[2], parts[4]) else NotesTarget.Table(parts[2])
            else -> return null
        }
        return NotesLink(target, host)
    }

    private fun query(uri: URI): Map<String, String> = (uri.rawQuery ?: "").split('&').filter { it.isNotEmpty() }.associate { kv ->
        val (k, v) = kv.split('=', limit = 2).let { it[0] to it.getOrElse(1) { "" } }
        URLDecoder.decode(k, "UTF-8") to URLDecoder.decode(v, "UTF-8")
    }

    /** The custom-scheme address of a note or table (shortcuts, widgets, "Open in Notes app"). */
    fun noteUri(id: String, item: String? = null) = "$SCHEME://note/$id" + (item?.let { "/item/$it" } ?: "")
    fun tableUri(id: String, row: String? = null) = "$SCHEME://table/$id" + (row?.let { "/row/$it" } ?: "")
    fun newUri(kind: String) = "$SCHEME://new?kind=$kind"
}
