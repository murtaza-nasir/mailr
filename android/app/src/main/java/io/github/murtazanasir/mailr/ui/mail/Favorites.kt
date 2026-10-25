package io.github.murtazanasir.mailr.ui.mail

import io.github.murtazanasir.mailr.data.api.Label
import kotlinx.serialization.json.JsonArray
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.JsonPrimitive
import kotlinx.serialization.json.contentOrNull
import io.github.murtazanasir.mailr.R

// Favorites in the drawer: an ordered list of view keys kept in the user's
// settings (`nav_favorites`), so the phone shows the same list as the web. The rules here are
// the web's (frontend/src/lib/favorites.ts).

val DEFAULT_FAVORITES = listOf("inbox", "sent", "drafts")

/** The folders a favorite may name, with the drawer's words for them. */
internal val FAVORITE_FOLDERS = linkedMapOf(
    "inbox" to R.string.mail_folder_inbox, "starred" to R.string.mail_folder_starred, "snoozed" to R.string.mail_folder_snoozed, "important" to R.string.mail_folder_important, "sent" to R.string.mail_folder_sent, "drafts" to R.string.mail_folder_drafts, "scheduled" to R.string.mail_folder_scheduled, "all" to R.string.mail_folder_all, "spam" to R.string.mail_folder_spam, "trash" to R.string.mail_folder_trash,
)

internal val FAVORITE_CATEGORIES = linkedMapOf(
    "primary" to R.string.mail_category_primary, "purchases" to R.string.mail_category_purchases, "social" to R.string.mail_category_social, "updates" to R.string.mail_category_updates, "forums" to R.string.mail_category_forums, "promotions" to R.string.mail_category_promotions,
)

private val LABEL_KEY = Regex("""label:\d+""")

/** Views a favorite may name: a folder, a category or a user label. */
fun canFavorite(key: String): Boolean = when {
    key.startsWith("label:") -> LABEL_KEY.matches(key)
    key.startsWith("category:") -> key.removePrefix("category:") in FAVORITE_CATEGORIES
    else -> key in FAVORITE_FOLDERS
}

/** The stored list from the settings; null while the setting is missing (the default applies). */
fun storedFavorites(settings: JsonObject?): List<String>? =
    (settings?.get("nav_favorites") as? JsonArray)?.mapNotNull { (it as? JsonPrimitive)?.contentOrNull }

/** The list as the drawer shows it: known views only; deleted labels drop out silently. */
fun resolveFavorites(keys: List<String>?, labels: List<Label>, labelsLoaded: Boolean = labels.isNotEmpty()): List<String> {
    val ids = labels.filter { it.role == null }.map { it.id }.toSet()
    return (keys ?: DEFAULT_FAVORITES).distinct().filter { k ->
        when {
            !canFavorite(k) -> false
            // While labels load, label favorites wait rather than flash out.
            k.startsWith("label:") -> labelsLoaded && k.removePrefix("label:").toLongOrNull() in ids
            else -> true
        }
    }
}

fun addFavorite(list: List<String>, key: String): List<String> = if (key in list) list else list + key

fun removeFavorite(list: List<String>, key: String): List<String> = list - key

/** One step up (-1) or down (+1); the ends stay put. */
fun moveFavorite(list: List<String>, key: String, delta: Int): List<String> {
    val i = list.indexOf(key)
    val j = i + delta
    if (i < 0 || j < 0 || j >= list.size) return list
    return list.toMutableList().apply { this[i] = list[j]; this[j] = list[i] }
}
