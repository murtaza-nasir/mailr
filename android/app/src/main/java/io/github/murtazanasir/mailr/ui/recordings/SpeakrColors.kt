package io.github.murtazanasir.mailr.ui.recordings

import androidx.compose.material3.MaterialTheme
import androidx.compose.runtime.Composable
import androidx.compose.runtime.ReadOnlyComposable
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.luminance

// Speakr's colours (speakr static/css/styles.css speaker-color-1..16, models/organization.py),
// so a recording looks the same in mailr as in Speakr: speakers take the next of sixteen colours
// in the order they first speak, tags and folders are filled with their own colour.

/** A speaker's colours: the tablet's background and its text. */
data class SpeakerColor(val bg: Color, val fg: Color)

private fun c(hex: Long) = Color(0xFF000000 or hex)

private val LIGHT = listOf(
    0xE3F2FD to 0x0D47A1, 0xF3E5F5 to 0x6A1B9A, 0xE8F5E9 to 0x1B5E20, 0xFFF3E0 to 0xE65100,
    0xFCE4EC to 0xAD1457, 0xE0F7FA to 0x006064, 0xFFF9C4 to 0xF57F17, 0xEFEBE9 to 0x5D4037,
    0xE8EAF6 to 0x283593, 0xF1F8E9 to 0x558B2F, 0xFFEBEE to 0xC62828, 0xECEFF1 to 0x37474F,
    0xFFF8E1 to 0xFF8F00, 0xEDE7F6 to 0x4527A0, 0xE0F2F1 to 0x00695C, 0xFBE9E7 to 0xBF360C,
).map { (b, f) -> SpeakerColor(c(b.toLong()), c(f.toLong())) }

private val DARK = listOf(
    0x1E3A5F to 0xA5C9EA, 0x4A2C5A to 0xD4A5D4, 0x1F4A3C to 0xA8D5A8, 0x5A3A1F to 0xE6B366,
    0x5A2C3E to 0xE6A5C4, 0x1F4A47 to 0xA5D5D0, 0x4A4A1F to 0xE6E266, 0x3E2723 to 0xD7CCC8,
    0x1A237E to 0x9FA8DA, 0x33691E to 0xC5E1A5, 0x5A1F1F to 0xEF9A9A, 0x263238 to 0xB0BEC5,
    0x4A3F1F to 0xFFE082, 0x311B92 to 0xB39DDB, 0x004D40 to 0x80CBC4, 0x4E2C1F to 0xFFAB91,
).map { (b, f) -> SpeakerColor(c(b.toLong()), c(f.toLong())) }

/** Colour index per speaker, in order of first appearance (Speakr's speakers.js). */
fun speakerIndex(speakers: List<String>): Map<String, Int> {
    val out = LinkedHashMap<String, Int>()
    speakers.forEach { if (it.isNotBlank() && it !in out) out[it] = out.size % LIGHT.size }
    return out
}

@Composable
@ReadOnlyComposable
fun speakerColor(index: Int): SpeakerColor {
    val dark = MaterialTheme.colorScheme.surface.luminance() < 0.5f
    return (if (dark) DARK else LIGHT)[index.mod(LIGHT.size)]
}

/** A tag's or folder's colour ("#3B82F6"), or [fallback]. */
fun labelColor(hex: String?, fallback: Color): Color =
    hex?.trim()?.removePrefix("#")?.takeIf { it.length == 6 }?.toLongOrNull(16)?.let { c(it) } ?: fallback

val TAG_FALLBACK = c(0x6B7280)
val FOLDER_FALLBACK = c(0x10B981)

/** Black or white text on [bg] (Speakr's getContrastTextColor: luminance above 0.65 → black). */
fun contrastOn(bg: Color): Color {
    val l = 0.299f * bg.red + 0.587f * bg.green + 0.114f * bg.blue
    return if (l > 0.65f) Color.Black else Color.White
}
