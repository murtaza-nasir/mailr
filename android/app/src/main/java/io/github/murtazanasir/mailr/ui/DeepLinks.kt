package io.github.murtazanasir.mailr.ui

// Deep links: where a notification, a citation, a mailr:// link or a web link wants to go (split out of App.kt).

import android.content.Intent
import android.net.Uri
import io.github.murtazanasir.mailr.ui.compose.ComposeArgs
import io.github.murtazanasir.mailr.ui.compose.Mailto


/** Where a deep link or notification wants to go. */
sealed interface Destination {
    data class Conversation(val id: Long, val messageId: Long? = null, val quote: String? = null) : Destination
    data class Compose(val args: ComposeArgs) : Destination
    data object Approvals : Destination
    /** One approval, from its notification: the Approvals screen scrolled to it (its NotifyDecisions.approvalKey). */
    data class ApprovalItem(val key: String) : Destination
    data object Inbox : Destination
    /** Scheduled (the outbox): a failed send's notification. */
    data object Scheduled : Destination
    /** New recording (the notification of a recording in progress). */
    data object Record : Destination
    /** The calendar: an event (uid, occurrence), or a view (`day`, `ndays`, `workweek`, `week`, `month`, `schedule`) and a date. */
    data class Event(val uid: String?, val occurrence: String?, val view: String? = null, val date: String? = null,
                     /** ?prep=1: the event's meeting prep opens with it (event.prep). */
                     val prep: Boolean = false) : Destination
    /** Tasks: a task, or a list in the web's route form (`today`, `l/<id>`, `t/<tag>`). */
    data class Task(val uid: String?, val view: String? = null) : Destination
    /** Notes: a note (with an item focused) or a table (with a row's form open). */
    data class Notes(val kind: String, val id: String, val sub: String? = null) : Destination
}

/** A note or table address: the web's `/notes/n/<id>` and `/tables/t/<id>` (an item after `#i-`), or `mailr://notes/n/<id>?item=…`. */
fun notesLink(segments: List<String>, fragment: String?, item: String?): Destination.Notes? {
    val s = segments.filter { it.isNotBlank() }
    val id = s.getOrNull(2)?.takeIf { Regex("^[nt][A-Za-z0-9]+$").matches(it) } ?: return null
    val sub = item?.takeIf { it.isNotBlank() } ?: fragment?.removePrefix("i-")?.removePrefix("r-")?.takeIf { it.isNotBlank() }
    return when {
        s.getOrNull(1) == "n" && id.startsWith("n") -> Destination.Notes("note", id, sub)
        s.getOrNull(1) == "t" && id.startsWith("t") -> Destination.Notes("table", id, sub)
        else -> null
    }
}

/**
 * A calendar or tasks link in the web's URL form, from its path segments after the host:
 * `calendar/event/<uid>/<occ>`, `calendar/<view>/<date>`, `calendar/search`, `tasks/task/<uid>`,
 * `tasks/<list>[/task/<uid>]` (a list is `today`, `l/<id>` or `t/<tag>`).
 */
fun appLink(segments: List<String>): Destination? {
    val s = segments.filter { it.isNotEmpty() }
    return when (s.firstOrNull()) {
        "calendar" -> when {
            s.getOrNull(1) == "event" && s.size >= 3 -> Destination.Event(s[2], s.getOrNull(3))
            s.getOrNull(1) == "search" -> Destination.Event(null, null)
            else -> Destination.Event(null, null, s.getOrNull(1), s.getOrNull(2))
        }
        "tasks" -> {
            val rest = s.drop(1)
            val at = rest.indexOf("task")
            val uid = if (at >= 0) rest.getOrNull(at + 1) else null
            val list = (if (at >= 0) rest.take(at) else rest).joinToString("/").ifEmpty { null }
            Destination.Task(uid, list)
        }
        else -> null
    }
}

/**
 * Where an intent wants to go. [serverHost] is the host of the signed-in account: an https
 * link (a conversation, calendar or tasks link) opens only when it points at that server.
 * [internal]: the intent came from the app itself ([io.github.murtazanasir.mailr.ui.util.InternalLinks]);
 * only then do the notification-only links open (mailr://approvals, record, scheduled,
 * conversation). Any other app can start MainActivity with any data.
 */
fun parseDeepLink(intent: Intent?, serverHost: String? = null, internal: Boolean = false): Destination? {
    val data = intent?.data ?: return null
    val path = data.path.orEmpty()
    val onServer = data.scheme == "https" && serverHost != null && data.host.equals(serverHost, ignoreCase = true)
    if (data.scheme == "mailr" && data.host in INTERNAL_HOSTS && !internal) return null
    return when {
        data.scheme == "https" && (path.startsWith("/calendar") || path.startsWith("/tasks")) ->
            if (serverHost != null && data.host.equals(serverHost, ignoreCase = true)) withPrep(appLink(data.pathSegments), data) else null
        // ACT compose.deeplink @android.editor: a mailto: link from any app (VIEW or SENDTO, with the
        // extras some apps add) opens the composer filled in (RFC 6068, ui/compose/Mailto.kt). Nothing is sent.
        data.scheme.equals("mailto", ignoreCase = true) -> {
            val link = Mailto.parse(data.toString()) ?: Mailto.Fields()
            val f = Mailto.withExtras(link,
                intent.getStringArrayExtra(Intent.EXTRA_EMAIL)?.toList(), intent.getStringArrayExtra(Intent.EXTRA_CC)?.toList(),
                intent.getStringArrayExtra(Intent.EXTRA_BCC)?.toList(), intent.getStringExtra(Intent.EXTRA_SUBJECT),
                intent.getCharSequenceExtra(Intent.EXTRA_TEXT)?.toString())
            Destination.Compose(ComposeArgs(to = Mailto.listText(f.to), subject = f.subject.ifEmpty { null }, body = f.body.ifEmpty { null },
                cc = Mailto.listText(f.cc), bcc = Mailto.listText(f.bcc)))
        }
        data.scheme == "mailr" && data.host == "conversation" -> convFromSlug(data.lastPathSegment)?.let { Destination.Conversation(it) }
        data.scheme == "mailr" && data.host == "approvals" ->
            data.lastPathSegment?.takeIf { it.isNotBlank() }?.let { Destination.ApprovalItem(it) } ?: Destination.Approvals
        data.scheme == "mailr" && data.host == "record" -> Destination.Record
        data.scheme == "mailr" && data.host == "scheduled" -> Destination.Scheduled
        data.scheme == "mailr" && data.host == "event" ->
            Destination.Event(data.lastPathSegment, data.getQueryParameter("occ")?.ifBlank { null }, prep = data.getQueryParameter("prep") == "1")
        data.scheme == "mailr" && data.host == "task" -> Destination.Task(data.lastPathSegment)
        // mailr://calendar/…, mailr://tasks/…: the web's paths under the app's scheme.
        data.scheme == "mailr" && (data.host == "calendar" || data.host == "tasks") -> withPrep(appLink(listOf(data.host!!) + data.pathSegments), data)
        data.scheme == "mailr" && (data.host == "notes" || data.host == "tables") ->
            notesLink(listOf(data.host!!) + data.pathSegments, data.fragment, data.getQueryParameter("item") ?: data.getQueryParameter("row"))
                ?: Destination.Notes("grid", "")
        data.scheme == "mailr" -> Destination.Inbox
        !onServer -> null
        data.path?.startsWith("/notes/") == true || data.path?.startsWith("/tables/") == true ->
            notesLink(data.pathSegments, data.fragment, null)
        data.path?.startsWith("/c/") == true -> convFromSlug(data.pathSegments.getOrNull(1))?.let {
            Destination.Conversation(it, data.getQueryParameter("m")?.toLongOrNull(), data.getQueryParameter("q"))
        }
        // Web links: /mail/<view>/<conversation> (the id in its URL form, see convFromSlug).
        data.path?.startsWith("/mail/") == true && data.pathSegments.size >= 3 -> convFromSlug(data.pathSegments[2])?.let {
            Destination.Conversation(it, data.getQueryParameter("m")?.toLongOrNull(), data.getQueryParameter("q"))
        }
        else -> null
    }
}

/** An event link with ?prep=1 (the digest's links, a reminder's Prep) opens its meeting prep (event.prep). */
internal fun withPrep(d: Destination?, data: Uri): Destination? =
    if (d is Destination.Event && d.uid != null && data.getQueryParameter("prep") == "1") d.copy(prep = true) else d

/** mailr:// hosts only the app's own notifications open (see [parseDeepLink]). */
internal val INTERNAL_HOSTS = setOf("approvals", "record", "scheduled", "conversation")

internal fun parseMailtoArgs(uri: Uri): ComposeArgs =
    (parseDeepLink(Intent(Intent.ACTION_VIEW, uri)) as? Destination.Compose)?.args ?: ComposeArgs()

internal const val B62 = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz"

/** A conversation id from a URL: a plain number, or the server's "c" + base62 form of a public
 *  id (core/publicid.py, web lib/pubid.ts). */
internal fun convFromSlug(slug: String?): Long? {
    if (slug.isNullOrBlank()) return null
    slug.toLongOrNull()?.let { return it }
    // Public ids are below 2^53, at most 9 base62 digits; anything longer would overflow a Long.
    if (!Regex("^c[0-9A-Za-z]{2,9}$").matches(slug)) return null
    var v = 0L
    for (ch in slug.substring(1)) v = v * 62 + B62.indexOf(ch)
    return v.takeIf { it in 1 until (1L shl 53) }
}
