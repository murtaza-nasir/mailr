package io.github.murtazanasir.mailr.data.api

import kotlinx.serialization.KSerializer
import kotlinx.serialization.descriptors.PrimitiveKind
import kotlinx.serialization.descriptors.PrimitiveSerialDescriptor
import kotlinx.serialization.descriptors.SerialDescriptor
import kotlinx.serialization.encoding.Decoder
import kotlinx.serialization.encoding.Encoder

/**
 * Text written by other people (display names, subjects, file names) without the characters
 * that reorder or hide what is shown: the bidirectional embeddings, overrides and isolates
 * (U+202A–202E, U+2066–2069), the direction marks (U+200E, U+200F, U+061C) and other control
 * characters. A name like "Bob U+202E moc.lapyap@ecivres" would otherwise show the
 * reversed text as an address and reverse the real address next to it.
 */
object SafeText {
    private val UNSAFE = Regex("[\\u202A-\\u202E\\u2066-\\u2069\\u200E\\u200F\\u061C\\u0000-\\u0008\\u000B\\u000C\\u000E-\\u001F\\u007F-\\u009F]")

    /** [clean] that keeps line breaks and tabs: for previews, where a line end still means a line end. */
    fun cleanKeepLines(s: String): String = if (s.isEmpty()) s else UNSAFE.replace(s, "")

    fun clean(s: String): String = if (s.isEmpty()) s else UNSAFE.replace(s, "").replace('\t', ' ').replace('\n', ' ').replace('\r', ' ')

    /** Bidi controls, invisible characters, joiners, control characters and white space: an email
     *  address has no use for any of them, and they make it read as another one (backend
     *  core/textsafe.clean_address, web safeAddress). */
    private val ADDRESS_UNSAFE = Regex(
        "[\\u202A-\\u202E\\u2066-\\u2069\\u200E\\u200F\\u061C\\u200B\\u2060-\\u2064\\uFEFF\\u180E\\u00AD\\u034F" +
            "\\u115F\\u1160\\u3164\\uFFA0\\u200C\\u200D\\s\\u0000-\\u001F\\u007F-\\u009F]",
    )

    /** An email address without the characters in [ADDRESS_UNSAFE]. */
    fun cleanAddress(s: String): String = if (s.isEmpty()) s else ADDRESS_UNSAFE.replace(s, "")

    /** First-strong isolate around [s], so right-to-left text in it cannot reorder what follows. */
    fun isolate(s: String): String = "\u2068${clean(s)}\u2069"

    /** File types that run code or open as a web page when tapped. */
    private val RISKY = setOf(
        "exe", "scr", "com", "bat", "cmd", "pif", "msi", "msp", "cpl", "dll", "js", "jse", "vbs", "vbe", "wsf", "wsh",
        "ps1", "hta", "lnk", "iso", "img", "vhd", "vhdx", "jar", "apk", "xapk", "html", "htm", "shtml", "svg", "xhtml",
        "reg", "app", "dmg", "pkg", "sh", "command", "url", "desktop", "appx", "msix", "xll", "chm",
    )

    /** The extension of [filename] when it is a risky one (lower case), else null. */
    fun riskyExtension(filename: String): String? =
        clean(filename).substringAfterLast('.', "").trim().lowercase().takeIf { it in RISKY }
}

/** A string field decoded (and encoded) through [SafeText.clean]. */
object SafeTextSerializer : KSerializer<String> {
    override val descriptor: SerialDescriptor = PrimitiveSerialDescriptor("io.github.murtazanasir.mailr.SafeText", PrimitiveKind.STRING)
    override fun deserialize(decoder: Decoder): String = SafeText.clean(decoder.decodeString())
    override fun serialize(encoder: Encoder, value: String) = encoder.encodeString(SafeText.clean(value))
}

/** An address field decoded (and encoded) through [SafeText.cleanAddress]. */
object SafeAddressSerializer : KSerializer<String> {
    override val descriptor: SerialDescriptor = PrimitiveSerialDescriptor("io.github.murtazanasir.mailr.SafeAddress", PrimitiveKind.STRING)
    override fun deserialize(decoder: Decoder): String = SafeText.cleanAddress(decoder.decodeString())
    override fun serialize(encoder: Encoder, value: String) = encoder.encodeString(SafeText.cleanAddress(value))
}

/** A preview (snippet) field decoded through [SafeText.cleanKeepLines]: no bidi controls, line breaks kept. */
object SafeSnippetSerializer : KSerializer<String> {
    override val descriptor: SerialDescriptor = PrimitiveSerialDescriptor("io.github.murtazanasir.mailr.SafeSnippet", PrimitiveKind.STRING)
    override fun deserialize(decoder: Decoder): String = SafeText.cleanKeepLines(decoder.decodeString())
    override fun serialize(encoder: Encoder, value: String) = encoder.encodeString(SafeText.cleanKeepLines(value))
}
