package io.github.murtazanasir.mailr.ui.compose

import io.github.murtazanasir.mailr.data.api.Address
import java.nio.ByteBuffer
import java.nio.charset.Charset
import java.nio.charset.CodingErrorAction
import java.util.Base64

/**
 * mailto: links (RFC 6068), which open mailr's composer: the twin of the web's lib/mailto.ts, run on
 * the same cases (spec/cases/mailto-cases.yaml; MailtoTest).
 *
 * - The scheme is case-insensitive. Before `?`: addresses, separated by commas.
 * - `to`, `cc` and `bcc` fields add addresses (they may repeat); `subject` and `body` set the text.
 *   Field names are case-insensitive. Every other field is ignored.
 * - Percent-decoding is UTF-8; `+` stays a plus sign; a broken escape is kept as written.
 * - Encoded words (RFC 2047) in the subject and in display names are decoded.
 * - An address needs an "@"; others are dropped. Each address is kept once per field.
 * - Addresses lose bidi controls and invisible characters, names lose bidi controls.
 * - At most 50 recipients (To, Cc and Bcc together) and 50,000 characters of body.
 * - A link only fills a draft. Nothing is sent until the user presses Send.
 */
object Mailto {
    data class Fields(
        val to: List<Address> = emptyList(),
        val cc: List<Address> = emptyList(),
        val bcc: List<Address> = emptyList(),
        val subject: String = "",
        val body: String = "",
    )

    /** Recipients a link fills, To, Cc and Bcc together. */
    const val MAX_RECIPIENTS = 50
    private const val MAX_SUBJECT = 998
    /** Characters of body text a link fills. */
    const val MAX_BODY = 50_000
    private val ADDR = Regex("^[^\\s@<>(),;:\"]+@[^\\s@<>(),;:\"]+$")
    // Bidi controls and invisible characters (the web's lib/safeText.ts DROP, plus joiners): an
    // address loses them, so a link cannot show one address and fill another.
    private val INVISIBLE = Regex("[\\u202A-\\u202E\\u2066-\\u2069\\u200E\\u200F\\u061C\\u200B-\\u200D\\u2060-\\u2064\\uFEFF\\u180E\\u00AD\\u034F\\u115F\\u1160\\u3164\\uFFA0]")
    private val INVISIBLE_IN_NAME = Regex("[\\u202A-\\u202E\\u2066-\\u2069\\u200E\\u200F\\u061C\\u200B\\u2060-\\u2064\\uFEFF\\u180E\\u00AD\\u034F\\u115F\\u1160\\u3164\\uFFA0]")
    private val CONTROL = Regex("[\\u0000-\\u0008\\u000B\\u000C\\u000E-\\u001F\\u007F-\\u009F]")

    /** An address without bidi controls and invisible characters; a control character becomes a
     *  space, so the address is then refused. */
    private fun cleanAddress(s: String) = CONTROL.replace(INVISIBLE.replace(s, ""), " ")
    private val WORD = Regex("=\\?([^?\\s]+)\\?([bBqQ])\\?([^?\\s]*)\\?=")
    private val BETWEEN_WORDS = Regex("(=\\?[^?\\s]+\\?[bBqQ]\\?[^?\\s]*\\?=)\\s+(?==\\?[^?\\s]+\\?[bBqQ]\\?[^?\\s]*\\?=)")
    private val ESCAPES = Regex("(?:%[0-9A-Fa-f]{2})+")

    private fun utf8(bytes: ByteArray): String? = decode(bytes, Charsets.UTF_8)

    private fun decode(bytes: ByteArray, cs: Charset): String? = try {
        cs.newDecoder().onMalformedInput(CodingErrorAction.REPORT).onUnmappableCharacter(CodingErrorAction.REPORT)
            .decode(ByteBuffer.wrap(bytes)).toString()
    } catch (_: Exception) {
        null
    }

    /** Percent-decoding as UTF-8; a run of escapes that does not decode stays as written. */
    fun pctDecode(s: String): String = ESCAPES.replace(s) { m ->
        val bytes = m.value.chunked(3).map { it.substring(1).toInt(16).toByte() }.toByteArray()
        utf8(bytes) ?: m.value
    }

    /** RFC 2047 encoded words ("=?utf-8?Q?Caf=C3=A9?="); whitespace between two words goes. */
    fun decodeEncodedWords(s: String): String {
        if (!s.contains("=?")) return s
        return WORD.replace(BETWEEN_WORDS.replace(s, "$1")) { m ->
            val (charset, enc, text) = m.destructured
            val cs = try {
                Charset.forName(charset.substringBefore('*'))
            } catch (_: Exception) {
                null
            } ?: return@replace m.value
            val bytes = if (enc.equals("B", ignoreCase = true)) {
                try {
                    Base64.getDecoder().decode(text + "=".repeat((4 - text.length % 4) % 4))
                } catch (_: Exception) {
                    return@replace m.value
                }
            } else {
                val q = text.replace('_', ' ')
                val out = ArrayList<Byte>()
                var i = 0
                while (i < q.length) {
                    val hex = if (q[i] == '=' && i + 3 <= q.length) q.substring(i + 1, i + 3) else null
                    if (hex != null && hex.all { it.isDigit() || it in 'a'..'f' || it in 'A'..'F' }) {
                        out += hex.toInt(16).toByte()
                        i += 3
                    } else {
                        out += (q[i].code and 0xff).toByte()
                        i++
                    }
                }
                out.toByteArray()
            }
            decode(bytes, cs) ?: m.value
        }
    }

    /** Split an address list on commas (or semicolons) outside quotes and angle brackets. */
    private fun splitList(s: String): List<String> {
        val out = mutableListOf<String>()
        val cur = StringBuilder()
        var quoted = false
        var angle = 0
        for (ch in s) {
            if (ch == '"') quoted = !quoted
            else if (!quoted && ch == '<') angle++
            else if (!quoted && ch == '>') angle = maxOf(0, angle - 1)
            if (!quoted && angle == 0 && (ch == ',' || ch == ';')) {
                out += cur.toString()
                cur.clear()
            } else cur.append(ch)
        }
        out += cur.toString()
        return out.map { it.trim() }.filter { it.isNotEmpty() }
    }

    /** "Name <a@b>", "\"Last, First\" <a@b>" or "a@b" → an address; null for anything else. */
    fun parseOneAddress(raw: String): Address? {
        val s = decodeEncodedWords(raw.trim())
        val m = Regex("^(.*?)<([^<>]+)>\\s*$", RegexOption.DOT_MATCHES_ALL).find(s)
        val email = cleanAddress((m?.groupValues?.get(2) ?: s).trim())
        if (!ADDR.matches(email)) return null
        val name = (m?.groupValues?.get(1) ?: "").trim().let { n ->
            if (n.length >= 2 && n.startsWith('"') && n.endsWith('"')) n.substring(1, n.length - 1) else n
        }.replace(Regex("\\\\(.)"), "$1").let { CONTROL.replace(INVISIBLE_IN_NAME.replace(it, ""), " ") }.trim()
        return Address(email, name.ifEmpty { null })
    }

    fun parseAddresses(value: String?): List<Address> =
        if (value.isNullOrBlank()) emptyList() else splitList(value).mapNotNull { parseOneAddress(it) }

    /** Adds [more] to this list once each, while the three lists hold fewer than MAX_RECIPIENTS. */
    private fun MutableList<Address>.addAllOnce(more: List<Address>, total: () -> Int) {
        for (a in more) {
            if (total() >= MAX_RECIPIENTS) return
            if (none { it.email.equals(a.email, ignoreCase = true) }) add(a)
        }
    }

    private fun oneLine(s: String) = s.replace(Regex("[\\r\\n]+"), " ").take(MAX_SUBJECT)
    private fun bodyText(s: String) = s.replace(Regex("\\r\\n?"), "\n").take(MAX_BODY)

    /** A mailto: URL → its fields, or null when it is not a mailto: URL. */
    fun parse(url: String): Fields? {
        val m = Regex("^\\s*mailto:(.*)$", setOf(RegexOption.IGNORE_CASE, RegexOption.DOT_MATCHES_ALL)).find(url) ?: return null
        val rest = m.groupValues[1].substringBefore('#')
        val path = rest.substringBefore('?')
        val query = rest.substringAfter('?', "")
        val to = mutableListOf<Address>()
        val cc = mutableListOf<Address>()
        val bcc = mutableListOf<Address>()
        var subject = ""
        var body = ""
        val total = { to.size + cc.size + bcc.size }
        to.addAllOnce(parseAddresses(pctDecode(path)), total)
        for (part in query.split('&')) {
            if (part.isEmpty()) continue
            val name = pctDecode(part.substringBefore('=')).trim().lowercase()
            val value = pctDecode(part.substringAfter('=', ""))
            when (name) {
                "to" -> to.addAllOnce(parseAddresses(value), total)
                "cc" -> cc.addAllOnce(parseAddresses(value), total)
                "bcc" -> bcc.addAllOnce(parseAddresses(value), total)
                "subject" -> subject = oneLine(decodeEncodedWords(value))
                "body" -> body = bodyText(value)
            }
        }
        return Fields(to, cc, bcc, subject, body)
    }

    /**
     * The fields of a mailto: intent: the link, plus the extras that apps put beside it
     * (EXTRA_EMAIL, EXTRA_CC, EXTRA_BCC, EXTRA_SUBJECT, EXTRA_TEXT). The link's own subject and body win.
     */
    fun withExtras(f: Fields, email: List<String>?, cc: List<String>?, bcc: List<String>?, subject: String?, text: String?): Fields {
        val to = f.to.toMutableList()
        val ccs = f.cc.toMutableList()
        val bccs = f.bcc.toMutableList()
        val total = { to.size + ccs.size + bccs.size }
        to.addAllOnce(email.orEmpty().flatMap { parseAddresses(it) }, total)
        ccs.addAllOnce(cc.orEmpty().flatMap { parseAddresses(it) }, total)
        bccs.addAllOnce(bcc.orEmpty().flatMap { parseAddresses(it) }, total)
        return f.copy(
            to = to, cc = ccs, bcc = bccs,
            subject = f.subject.ifEmpty { oneLine(subject.orEmpty()) },
            body = f.body.ifEmpty { bodyText(text.orEmpty()) },
        )
    }

    /** One address as the composer's argument string holds it (parseAddresses reads it back). */
    fun addressText(a: Address): String =
        if (a.name.isNullOrBlank()) a.email else "\"" + a.name.replace("\\", "\\\\").replace("\"", "\\\"") + "\" <" + a.email + ">"

    fun listText(list: List<Address>): String? = list.takeIf { it.isNotEmpty() }?.joinToString(", ") { addressText(it) }
}
