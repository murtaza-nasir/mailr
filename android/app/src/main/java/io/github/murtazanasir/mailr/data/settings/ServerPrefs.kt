package io.github.murtazanasir.mailr.data.settings

import io.github.murtazanasir.mailr.i18n.AppText
import io.github.murtazanasir.mailr.R
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.JsonPrimitive
import kotlinx.serialization.json.booleanOrNull
import kotlinx.serialization.json.contentOrNull

/**
 * The account settings the phone follows: what notifies (`mail_notifications`,
 * `notify_approvals`; one choice for every device, the server's core/notifyprefs.py) and the
 * remote-image choice (`images`). GET /me carries them; the phone keeps a copy in [Prefs] for the
 * poll that runs without a network, and changes them on the server (PATCH /me/settings), never only
 * here. What stays on the phone: "Mail on this phone" off ([Prefs.phoneMailOff]) and an image
 * override ([Prefs.imagesOverride]).
 */
object ServerPrefs {
    private fun str(s: JsonObject, k: String) = (s[k] as? JsonPrimitive)?.contentOrNull
    private fun bool(s: JsonObject, k: String) = (s[k] as? JsonPrimitive)?.booleanOrNull

    /** The account's mail level; an older server's `desktop_notifications` when the new key is missing. */
    fun mailLevel(s: JsonObject): NotifyMode? =
        NotifyMode.of(str(s, "mail_notifications")) ?: NotifyMode.of(str(s, "desktop_notifications"))

    /** The phone's copy after GET /me. Keys the server does not send leave the copy alone. */
    fun apply(p: Prefs, s: JsonObject): Prefs {
        var n = p
        mailLevel(s)?.let { n = n.copy(notify = it) }
        bool(s, "notify_approvals")?.let { n = n.copy(notifyApprovals = it) }
        str(s, "images")?.takeIf { it in setOf("always", "ask", "never") }?.let { n = n.copy(imagesWeb = it) }
        webColours(s).let { (seed, harmony) -> n = n.copy(webSeed = seed, webHarmony = harmony) }
        return n
    }

    /** Each web style's own colour (the seed of its first preset, frontend/src/lib/colour/themes.ts). */
    val STYLE_SEEDS = mapOf("classic" to "#1f6c86", "office" to "#17607a", "focus" to "#5151cd", "desk" to "#1d6a8a", "cards" to "#6750a4")
    /** Cards' accents before 8 Oct 2026, when `ui_accent` was one of these names. */
    val LEGACY_CARDS = mapOf("violet" to "#6750a4", "teal" to "#006a6a", "rose" to "#984061", "green" to "#3b6a1f", "blue" to "#00629e")

    /**
     * The colours the web shows, as a seed and a harmony: the account's
     * `ui_accent` in its `ui_theme`, resolved as the web does (frontend/src/lib/colour/choice.ts). A
     * preset applies in its own style only, a colour of your own in every style, and nothing chosen
     * is the style's own colour.
     */
    fun webColours(s: JsonObject): Pair<String, String> {
        val style = str(s, "ui_theme")?.takeIf { it in STYLE_SEEDS } ?: "classic"
        val own = STYLE_SEEDS.getValue(style) to "single"
        val accent = s["ui_accent"]
        if (accent is JsonPrimitive) {
            val legacy = LEGACY_CARDS[accent.contentOrNull]
            return if (legacy != null && style == "cards") legacy to "single" else own
        }
        val o = accent as? JsonObject ?: return own
        val preset = str(o, "preset")
        val seed = str(o, "seed")?.lowercase()?.takeIf { io.github.murtazanasir.mailr.ui.theme.SeedScheme.isHex(it) }
        val harmony = str(o, "harmony")?.takeIf { h -> io.github.murtazanasir.mailr.ui.theme.SeedScheme.Harmony.entries.any { it.key == h } } ?: "single"
        if (preset != null) {
            if (!preset.startsWith("$style.")) return own
            val known = if (style == "cards") LEGACY_CARDS[preset.removePrefix("cards.")] else null
            return (seed ?: known ?: own.first) to harmony
        }
        return if (seed != null) seed to harmony else own
    }

    /**
     * Once, on the first GET /me after the update: the phone chose its own mail level before (it was
     * phone-only, default Important). "Off" stays this phone's own ([Prefs.phoneMailOff]), since
     * silencing every device would surprise. Another choice moves up to the account when the account
     * still has the default ("important"): the patch to send, or null. Returns the new prefs (marked
     * migrated) and the patch.
     */
    fun migrate(p: Prefs, s: JsonObject): Pair<Prefs, JsonObject?> {
        if (p.notifyMigrated) return p to null
        val local = p.notify
        val server = mailLevel(s)
        return when {
            local == NotifyMode.OFF -> p.copy(phoneMailOff = true, notifyMigrated = true) to null
            server == NotifyMode.IMPORTANT && local != NotifyMode.IMPORTANT ->
                p.copy(notifyMigrated = true) to JsonObject(mapOf("mail_notifications" to JsonPrimitive(local.server)))
            else -> p.copy(notifyMigrated = true) to null
        }
    }

    /** The account's language (`language`, a BCP 47 tag such as "de" or "pt-BR"); null follows the phone. */
    fun language(s: JsonObject): String? = str(s, "language")

    /** PATCH /me/settings body for a new mail level. */
    fun mailPatch(mode: NotifyMode) = JsonObject(mapOf("mail_notifications" to JsonPrimitive(mode.server)))

    fun approvalsPatch(on: Boolean) = JsonObject(mapOf("notify_approvals" to JsonPrimitive(on)))

    /** The web's image choice in the phone's words. */
    fun imagesLabel(web: String) = when (web) {
        "always" -> AppText.get(R.string.settings_images_always)
        "never" -> AppText.get(R.string.settings_images_never)
        else -> AppText.get(R.string.settings_images_ask)
    }
}
