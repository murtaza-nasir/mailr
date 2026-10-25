package io.github.murtazanasir.mailr.data.share

import io.github.murtazanasir.mailr.util.catching

import android.content.ContentResolver
import android.content.Intent
import android.net.Uri
import android.provider.OpenableColumns
import androidx.core.content.IntentCompat
import io.github.murtazanasir.mailr.R
import io.github.murtazanasir.mailr.i18n.AppText

/**
 * What another app handed us through ACTION_SEND / ACTION_SEND_MULTIPLE.
 *
 * Apps disagree about where things go: Chrome puts the page title in EXTRA_SUBJECT and the URL
 * in EXTRA_TEXT; others send "Title\nhttps://…" as text, EXTRA_TITLE only, a Spanned, a list
 * of texts, or files only in ClipData. [ShareParser] folds all of those into one shape.
 */
data class SharedContent(
    val subject: String? = null,
    val title: String? = null,
    val text: String? = null,
    val html: String? = null,
    val files: List<Uri> = emptyList(),
    val to: List<String> = emptyList(),
    val cc: List<String> = emptyList(),
    val bcc: List<String> = emptyList(),
) {
    val isEmpty: Boolean get() = subject.isNullOrBlank() && title.isNullOrBlank() && text.isNullOrBlank() &&
        html.isNullOrBlank() && files.isEmpty()
}

object ShareParser {
    fun isShare(intent: Intent?): Boolean =
        intent?.action == Intent.ACTION_SEND || intent?.action == Intent.ACTION_SEND_MULTIPLE

    fun parse(intent: Intent?, ownPackage: String): SharedContent? {
        if (!isShare(intent)) return null
        intent!!
        val texts = buildList {
            intent.getCharSequenceArrayListExtra(Intent.EXTRA_TEXT)?.forEach { add(it.toString()) }
            if (isEmpty()) intent.getCharSequenceExtra(Intent.EXTRA_TEXT)?.let { add(it.toString()) }
        }.map { it.trim() }.filter { it.isNotEmpty() }.distinct()

        val uris = LinkedHashSet<Uri>()
        if (intent.action == Intent.ACTION_SEND_MULTIPLE) {
            IntentCompat.getParcelableArrayListExtra(intent, Intent.EXTRA_STREAM, Uri::class.java)?.let { uris += it }
        } else {
            IntentCompat.getParcelableExtra(intent, Intent.EXTRA_STREAM, Uri::class.java)?.let { uris += it }
        }
        intent.clipData?.let { clip ->
            for (i in 0 until clip.itemCount) clip.getItemAt(i).uri?.let { uris += it }
        }
        return SharedContent(
            subject = intent.getStringExtra(Intent.EXTRA_SUBJECT)?.trim()?.ifBlank { null },
            title = intent.getStringExtra(Intent.EXTRA_TITLE)?.trim()?.ifBlank { null },
            text = texts.joinToString("\n\n").ifBlank { null },
            html = intent.getStringExtra(Intent.EXTRA_HTML_TEXT)?.ifBlank { null },
            files = uris.filter { safeUri(it, ownPackage) },
            to = intent.getStringArrayExtra(Intent.EXTRA_EMAIL)?.toList().orEmpty(),
            cc = intent.getStringArrayExtra(Intent.EXTRA_CC)?.toList().orEmpty(),
            bcc = intent.getStringArrayExtra(Intent.EXTRA_BCC)?.toList().orEmpty(),
        )
    }

    /**
     * Only content:// URIs from other apps. A file:// URI or one of our own providers would
     * let another app make us mail out our private files.
     */
    fun safeUri(uri: Uri, ownPackage: String): Boolean {
        if (uri.scheme != ContentResolver.SCHEME_CONTENT) return false
        // Content authorities may carry a user id ("0@io.github.murtazanasir.mailr.files"); compare the
        // provider part, so another app cannot hand us our own private files to send.
        val provider = uri.authority?.substringAfterLast('@')?.lowercase() ?: return false
        return !provider.startsWith(ownPackage.lowercase())
    }
}

/** Name, type and size of a shared file, from the provider (with sane fallbacks). */
data class FileMeta(val name: String, val type: String, val size: Long)

fun ContentResolver.fileMeta(uri: Uri): FileMeta {
    var name: String? = null
    var size = -1L
    catching {
        query(uri, arrayOf(OpenableColumns.DISPLAY_NAME, OpenableColumns.SIZE), null, null, null)?.use { c ->
            if (c.moveToFirst()) {
                name = c.getString(0)
                size = if (c.isNull(1)) -1 else c.getLong(1)
            }
        }
    }
    val type = catching { getType(uri) }.getOrNull() ?: "application/octet-stream"
    val fallback = "shared" + (android.webkit.MimeTypeMap.getSingleton().getExtensionFromMimeType(type)?.let { ".$it" } ?: "")
    return FileMeta(SelfSubject.safeFileName(name ?: uri.lastPathSegment ?: fallback, fallback), type, size)
}

/** Subject and body rules for "Send to self". Pure, so they are unit-tested. */
object SelfSubject {
    private val URL = Regex("""https?://[^\s<>"']+""", RegexOption.IGNORE_CASE)
    /**
     * The subject when nothing else names the share. ShareTask (ui/tasks) compares with it in a plain
     * JVM test, which has no resources; there it stays in English.
     */
    val FALLBACK: String get() = catching { AppText.get(R.string.sharing_note_to_self) }.getOrDefault("Note to self") // i18n-ignore: English fallback without resources

    fun firstUrl(text: String?): String? = text?.let { URL.find(it)?.value?.trimEnd('.', ',', ')', ';', '!', '?') }

    /** The text is nothing but a URL (what browsers share). */
    fun isLoneUrl(text: String?): Boolean {
        val t = text?.trim() ?: return false
        return t.isNotEmpty() && ' ' !in t && '\n' !in t && URL.matches(t)
    }

    /**
     * EXTRA_SUBJECT, then EXTRA_TITLE, then the fetched page title, then the first line of the
     * text that is not only a URL, then the URL itself (host and path), then the file names.
     */
    fun derive(subject: String?, title: String?, pageTitle: String?, text: String?, fileNames: List<String>, note: String? = null): String {
        listOf(subject, title, pageTitle).forEach { s -> clean(s)?.let { return it } }
        val lines = text.orEmpty().lines().map { it.trim() }.filter { it.isNotEmpty() }
        lines.firstOrNull { !isLoneUrl(it) }?.let { l -> clean(URL.replace(l, "").trim().trimEnd(':', '-', '–', '|').ifBlank { null } ?: l)?.let { return it } }
        firstUrl(text)?.let { return shortUrl(it) }
        clean(note?.lines()?.firstOrNull { it.isNotBlank() })?.let { return it }
        return when (fileNames.size) {
            0 -> FALLBACK
            1 -> fileNames[0]
            else -> AppText.plural(R.plurals.sharing_files_and_more_n, fileNames.size - 1, fileNames[0], fileNames.size - 1)
        }
    }

    fun shortUrl(url: String): String {
        val u = catching { java.net.URI(url) }.getOrNull() ?: return url.take(MAX)
        val host = u.host?.removePrefix("www.") ?: return url.take(MAX)
        val path = u.rawPath?.trimEnd('/').orEmpty()
        return (host + path).take(MAX)
    }

    /** Body text: the note first, then the shared text (or the URL), each once. */
    fun body(text: String?, note: String?, subject: String): String = buildString {
        note?.trim()?.takeIf { it.isNotEmpty() }?.let { append(it) }
        val t = text?.trim().orEmpty()
        if (t.isNotEmpty() && t != subject) {
            if (isNotEmpty()) append("\n\n")
            append(t)
        }
    }

    private fun clean(s: String?): String? = s?.replace(Regex("\\s+"), " ")?.trim()?.take(MAX)?.ifBlank { null }

    /** Page <title> or og:title from the first bytes of an HTML page. */
    fun titleFromHtml(html: String): String? {
        val og = Regex("""<meta[^>]+property=["']og:title["'][^>]*content=["']([^"']+)["']""", RegexOption.IGNORE_CASE).find(html)
            ?: Regex("""<meta[^>]+content=["']([^"']+)["'][^>]*property=["']og:title["']""", RegexOption.IGNORE_CASE).find(html)
        val raw = og?.groupValues?.get(1)
            ?: Regex("""<title[^>]*>([\s\S]*?)</title>""", RegexOption.IGNORE_CASE).find(html)?.groupValues?.get(1)
            ?: return null
        return clean(unescape(raw))
    }

    private fun unescape(s: String): String = s
        .replace(Regex("&#(\\d+);")) { m -> m.groupValues[1].toIntOrNull()?.let { String(Character.toChars(it)) } ?: m.value }
        .replace(Regex("&#x([0-9a-fA-F]+);")) { m -> m.groupValues[1].toIntOrNull(16)?.let { String(Character.toChars(it)) } ?: m.value }
        .replace("&quot;", "\"").replace("&#39;", "'").replace("&apos;", "'").replace("&lt;", "<").replace("&gt;", ">")
        .replace("&nbsp;", " ").replace("&amp;", "&")

    fun safeFileName(name: String, fallback: String = "shared"): String {
        val n = name.substringAfterLast('/').replace(Regex("[\\\\:*?\"<>|\\u0000-\\u001f]"), "_").trim().trim('.')
        return n.take(120).ifBlank { fallback }
    }

    private const val MAX = 150
}
